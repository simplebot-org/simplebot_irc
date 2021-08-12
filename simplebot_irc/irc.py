import string
import time
from threading import Thread
from typing import Dict

import irc.bot
import irc.client
from irc.client import ServerConnection
from simplebot.bot import DeltaBot, Replies

from .database import DBManager


class PuppetReactor(irc.client.SimpleIRCClient):
    def __init__(self, server, port, db: DBManager, dbot: DeltaBot) -> None:
        super().__init__()
        self.server = server
        self.port = port
        self.dbot = dbot
        self.db = db
        self.puppets: Dict[str, ServerConnection] = dict()
        for chan, gid in db.get_channels():
            for c in dbot.get_chat(gid).get_contacts():
                if dbot.self_contact == c:
                    continue
                self._get_puppet(c.addr).channels.add(chan)
        for addr in self.puppets:
            self._get_connected_puppet(addr)

    def _get_puppet(self, addr: str) -> irc.client.ServerConnection:
        cnn = self.puppets.get(addr)
        if not cnn:
            cnn = self.reactor.server()
            cnn.channels = set()
            cnn.addr = addr
            cnn.welcomed = False
            cnn.pending_actions = []
            self.puppets[addr] = cnn
        return cnn

    def _get_connected_puppet(self, addr: str) -> irc.client.ServerConnection:
        cnn = self._get_puppet(addr)
        if not cnn.is_connected():
            nick = self.db.get_nick(addr) + "|dc"
            cnn.connect(self.server, self.port, nick, ircname=nick)
        return cnn

    def _send_command(self, addr: str, command: str, *args) -> None:
        had_puppet = addr in self.puppets
        cnn = self._get_puppet(addr)
        if cnn.welcomed:
            getattr(cnn, command)(*args)
        else:
            cnn.pending_actions.append((command, *args))
            if not had_puppet:
                self._get_connected_puppet(addr)

    def _irc2dc(self, addr: str, e, impersonate: bool = True) -> None:
        if impersonate:
            sender = e.source.nick
        else:
            sender = None
        gid = self.db.get_pvchat(addr, e.source.nick)
        replies = Replies(self.dbot, logger=self.dbot.logger)
        replies.add(
            text=" ".join(e.arguments), sender=sender, chat=self.dbot.get_chat(gid)
        )
        replies.send_reply_messages()

    def set_nick(self, addr: str, nick: str) -> None:
        self.puppets[addr].nick(nick + "|dc")

    def join_channel(self, addr: str, channel: str) -> None:
        cnn = self._get_connected_puppet(addr)
        cnn.channels.add(channel)
        cnn.join(channel)

    def leave_channel(self, addr: str, channel: str) -> None:
        cnn = self._get_connected_puppet(addr)
        if channel in cnn.channels:
            cnn.channels.discard(channel)
            cnn.part(channel)
            if not cnn.channels:
                del self.puppets[addr]
                cnn.close()

    def send_message(self, addr: str, target: str, text: str) -> None:
        self._send_command(addr, "privmsg", target, text)

    def send_action(self, addr: str, target: str, text: str) -> None:
        self._send_command(addr, "action", target, text)

    # EVENTS:

    def on_nicknameinuse(self, c, e) -> None:
        nick = self.db.get_nick(c.addr)
        if len(nick) < 13:
            nick += "_"
        else:
            nick = nick[: len(nick) - 1]
        self.db.set_nick(c.addr, nick)
        c.nick(nick + "|dc")

    @staticmethod
    def on_welcome(c, e) -> None:
        c.welcomed = True
        for channel in c.channels:
            c.join(channel)
        while c.pendig_actions:
            args = c.pendig_actions.pop(0)
            getattr(c, args[0])(*args[1:])

    def on_privmsg(self, c, e) -> None:
        self._irc2dc(c.addr, e)

    def on_action(self, c, e) -> None:
        if not e.target.startswith(tuple("&#+!")):
            e.arguments.insert(0, "/me")
            self._irc2dc(c.addr, e)

    def on_nosuchnick(self, c, e) -> None:
        e.arguments = ["❌ " + ":".join(e.arguments)]
        self._irc2dc(c.addr, e, impersonate=False)

    def on_disconnect(self, c, e) -> None:
        c.welcomed = False
        if c.addr in self.puppets:
            time.sleep(5)
            self._get_connected_puppet(c.addr)  # reconnect


class IRCBot(irc.bot.SingleServerIRCBot):
    def __init__(
        self, server: str, port: int, nick: str, db: DBManager, dbot: DeltaBot
    ) -> None:
        nick = sanitize_nick(nick)
        self.nick = nick
        super().__init__([(server, port)], nick, nick)
        self.dbot = dbot
        self.db = db
        self.preactor = PuppetReactor(server, port, db, dbot)
        self.nick_counter = 1

    def on_nicknameinuse(self, c, e) -> None:
        self.nick_counter += 1
        nick = f"{self.nick}{self.nick_counter}"
        if len(nick) > 16:
            self.nick = self.nick[: len(self.nick) - 1]
            self.nick_counter = 1
            nick = self.nick
        c.nick(nick)

    def on_welcome(self, c, e) -> None:
        for chan, _ in self.db.get_channels():
            c.join(chan)
        Thread(target=self.preactor.start, daemon=True).start()

    def on_action(self, c, e) -> None:
        e.arguments.insert(0, "/me")
        self._irc2dc(e)

    def on_pubmsg(self, c, e) -> None:
        self._irc2dc(e)

    def _irc2dc(self, e) -> None:
        for cnn in self.preactor.puppets.values():
            if cnn.get_nickname() == e.source.nick:
                return
        gid = self.db.get_chat(e.target)
        if not gid:
            self.dbot.logger.warning("Chat not found for room: %s", e.target)
            return
        replies = Replies(self.dbot, logger=self.dbot.logger)
        replies.add(
            text=" ".join(e.arguments),
            sender=e.source.nick,
            chat=self.dbot.get_chat(gid),
        )
        replies.send_reply_messages()

    def on_notopic(self, c, e) -> None:
        chan = self.channels[e.arguments[0]]
        chan.topic = "-"

    def on_currenttopic(self, c, e) -> None:
        chan = self.channels[e.arguments[0]]
        chan.topic = e.arguments[1]

    def join_channel(self, name: str) -> None:
        self.connection.join(name)

    def leave_channel(self, channel: str) -> None:
        for addr in list(self.preactor.puppets.keys()):
            self.preactor.leave_channel(addr, channel)
        self.connection.part(channel)

    def get_topic(self, channel: str) -> str:
        self.connection.topic(channel)
        chan = self.channels[channel]
        if not hasattr(chan, "topic"):
            chan.topic = "-"
        return chan.topic

    def get_members(self, channel: str) -> list:
        return list(self.channels[channel].users())

    def send_message(self, target: str, text: str) -> None:
        self.connection.privmsg(target, text)


def sanitize_nick(nick: str) -> str:
    allowed = string.ascii_letters + string.digits + r"_-\[]{}^`|"
    return "".join(list(filter(allowed.__contains__, nick)))[:16]
