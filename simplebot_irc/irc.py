import string
import time
from threading import Thread
from typing import Dict, Tuple

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
        self.puppets: Dict[str, ServerConnection] = {}
        for chan, gid in db.get_channels():
            for c in dbot.get_chat(gid).get_contacts():
                if dbot.self_contact == c:
                    continue
                self._get_puppet(c.addr).channels.add(chan)
        for addr in self.puppets:
            self.dbot.logger.debug("[%s] Connecting puppet...", addr)
            self._get_connected_puppet(addr)
            time.sleep(2)

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

    def _reconnect(self, conn, _) -> bool:
        try:
            conn.welcomed = False
            if conn.addr in self.puppets:
                self.dbot.logger.warning(
                    f"Reconnecting: {conn.get_nickname()} ({conn.addr})"
                )
                time.sleep(15)
                self._get_connected_puppet(conn.addr)  # reconnect
            return True
        except irc.client.ServerConnectionError as err:
            self.dbot.logger.error("[%s] %s", conn.addr, err)
        return False

    def set_nick(self, addr: str, nick: str) -> None:
        if not self.puppets[addr]:
            self.puppets[addr].nick(nick + "|dc")
        else:
            self.dbot.logger.warning(f"User has no puppet: {addr}")

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

    def on_nicknameinuse(self, conn, _) -> None:
        nick = self.db.get_nick(conn.addr)
        if len(nick) < 13:
            nick += "_"
        else:
            nick = nick[: len(nick) - 1]
        self.db.set_nick(conn.addr, nick)
        conn.nick(nick + "|dc")

    @staticmethod
    def on_welcome(conn, _) -> None:
        conn.welcomed = True
        for channel in conn.channels:
            time.sleep(2)
            conn.join(channel)
        while conn.pending_actions:
            args = conn.pending_actions.pop(0)
            getattr(conn, args[0])(*args[1:])

    def on_privmsg(self, conn, event) -> None:
        self._irc2dc(conn.addr, event)

    def on_action(self, conn, event) -> None:
        if not event.target.startswith(tuple("&#+!")):
            event.arguments.insert(0, "/me")
            self._irc2dc(conn.addr, event)

    def on_nosuchnick(self, conn, event) -> None:
        event.arguments = ["❌ " + ":".join(event.arguments)]
        self._irc2dc(conn.addr, event, impersonate=False)

    def on_disconnect(self, conn, event) -> None:
        while not self._reconnect(conn, event):
            time.sleep(15)

    def on_error(self, conn, event) -> None:
        self.dbot.logger.error("[%s] %s", conn.addr, event)


class IRCBot(irc.bot.SingleServerIRCBot):
    def __init__(
        self, server: Tuple[str, int], nick: str, db: DBManager, dbot: DeltaBot
    ) -> None:
        nick = sanitize_nick(nick)
        self.nick = nick
        self.server, self.port = server
        super().__init__([(self.server, self.port)], nick, nick)
        self.dbot = dbot
        self.db = db
        self.preactor = PuppetReactor(self.server, self.port, db, dbot)
        self.nick_counter = 1

    def _irc2dc(self, event) -> None:
        for cnn in self.preactor.puppets.values():
            if cnn.get_nickname() == event.source.nick:
                return
        gid = self.db.get_chat(event.target)
        if not gid:
            self.dbot.logger.warning("Chat not found for room: %s", event.target)
            self.db.remove_channel(event.target)
            self.leave_channel(event.target)
            return
        replies = Replies(self.dbot, logger=self.dbot.logger)
        replies.add(
            text=" ".join(event.arguments),
            sender=event.source.nick,
            chat=self.dbot.get_chat(gid),
        )
        replies.send_reply_messages()

    def on_nicknameinuse(self, conn, _) -> None:
        self.nick_counter += 1
        nick = f"{self.nick}{self.nick_counter}"
        if len(nick) > 16:
            self.nick = self.nick[: len(self.nick) - 1]
            self.nick_counter = 1
            nick = self.nick
        conn.nick(nick)

    def on_welcome(self, conn, _) -> None:
        for chan, _ in self.db.get_channels():
            time.sleep(2)
            conn.join(chan)
        Thread(target=self.preactor.start, daemon=True).start()

    def on_action(self, _, event) -> None:
        event.arguments.insert(0, "/me")
        self._irc2dc(event)

    def on_pubmsg(self, _, event) -> None:
        self._irc2dc(event)

    def on_notopic(self, _, event) -> None:
        chan = self.channels[event.arguments[0]]
        chan.topic = "-"

    def on_currenttopic(self, _, event) -> None:
        chan = self.channels[event.arguments[0]]
        chan.topic = event.arguments[1]

    def on_error(self, _, event) -> None:
        self.dbot.logger.error("[bot] %s", event)

    def on_disconnect(self, conn, event) -> None:
        while not self._reconnect(conn, event):
            time.sleep(15)

    def _reconnect(self, conn, _) -> bool:
        try:
            self.dbot.logger.warning("[bot] Reconnecting...")
            time.sleep(15)
            conn.connect(self.server, self.port, self.nick, ircname=self.nick)
            return True
        except irc.client.ServerConnectionError as err:
            self.dbot.logger.error("[bot] %s", err)
        return False

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
