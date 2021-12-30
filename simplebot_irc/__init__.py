import functools
import io
import os
import re
from threading import Thread
from time import sleep
from typing import IO

import requests
import simplebot
from deltachat import Chat, Contact, Message
from pkg_resources import DistributionNotFound, get_distribution
from simplebot.bot import DeltaBot, Replies

from .database import DBManager
from .irc import IRCBot

try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
    # package is not installed
    __version__ = "0.0.0.dev0-unknown"
nick_re = re.compile(r"[-_a-zA-Z0-9]{1,30}$")
session = requests.Session()
session.headers.update(
    {
        "user-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:60.0) Gecko/20100101 Firefox/60.0"
    }
)
session.request = functools.partial(session.request, timeout=15)  # type: ignore
db: DBManager
irc_bridge: IRCBot


@simplebot.hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    _getdefault(bot, "uploads_url", "https://0x0.st/")
    _getdefault(bot, "nick", "DC-Bridge")
    _getdefault(bot, "host", "irc.libera.chat:6667")


@simplebot.hookimpl
def deltabot_start(bot: DeltaBot) -> None:
    global db, irc_bridge
    db = _get_db(bot)
    nick = _getdefault(bot, "nick")
    host_parts = _getdefault(bot, "host").split(":")
    host = host_parts[0]
    port = int(host_parts[1]) if len(host_parts) == 2 else 6667
    irc_bridge = IRCBot((host, port), nick, db, bot)
    bot.logger.debug("Sleeping 10 seconds to avoid throttle...")
    sleep(10)
    Thread(target=_run_irc, args=(bot,), daemon=True).start()


@simplebot.hookimpl
def deltabot_member_added(chat: Chat, contact: Contact) -> None:
    channel = db.get_channel_by_gid(chat.id)
    if channel:
        irc_bridge.preactor.join_channel(contact.addr, channel)


@simplebot.hookimpl
def deltabot_member_removed(bot: DeltaBot, chat: Chat, contact: Contact) -> None:
    channel = db.get_channel_by_gid(chat.id)
    if channel:
        contacts = chat.get_contacts()
        if bot.self_contact == contact or len(contacts) <= 1:
            db.remove_channel(channel)
            irc_bridge.leave_channel(channel)
            for cont in contacts:
                if cont != bot.self_contact:
                    irc_bridge.preactor.leave_channel(cont.addr, channel)
        else:
            irc_bridge.preactor.leave_channel(contact.addr, channel)
        return

    pvchat = db.get_pvchat_by_gid(chat.id)
    if pvchat:
        if bot.self_contact == contact or len(chat.get_contacts()) <= 1:
            db.remove_pvchat(pvchat["addr"], pvchat["nick"])


@simplebot.filter
def dc2irc(bot: DeltaBot, message: Message) -> None:
    """I will send to IRC any message you send in a Delta Chat group bridged to an IRC room.

    At the same time, I will send to the Delta Chat group any message sent in the bridged IRC room.
    """
    target = db.get_channel_by_gid(message.chat.id)
    if target:
        addr = message.get_sender_contact().addr
    else:
        pvchat = db.get_pvchat_by_gid(message.chat.id)
        if pvchat:
            target = pvchat["nick"]
            addr = pvchat["addr"]
    if not target:
        return

    quoted_msg = message.quote
    if quoted_msg:
        quoted_addr = quoted_msg.get_sender_contact().addr
        if quoted_addr == bot.self_contact.addr:
            quoted_nick = quoted_msg.override_sender_name or irc_bridge.nick
        else:
            quoted_nick = db.get_nick(quoted_addr)
        quote = " ".join(message.quoted_text.split("\n"))
        if len(quote) > 40:
            quote = quote[:40] + "..."
        text = f"<{quoted_nick}: {quote}> "
    else:
        text = ""
    if message.filename:
        url = _getdefault(bot, "uploads_url", "").strip()
        if url:
            with open(message.filename, "rb") as file:
                url = _upload(message.filename, file, url)
        if url:
            text += url
        else:
            text += "[File]"
        if message.text:
            text += " - "
    text += message.text
    if not text:
        return

    n = 450
    url = _getdefault(bot, "uploads_url", "").strip()
    if len(text) > n and url:
        with io.StringIO(text) as file2:
            url = _upload("long-text-message.txt", file2, url)
        irc_bridge.preactor.send_message(addr, target, f"Long message: {url}")
    else:
        text = " ".join(text.split("\n"))
        for fragment in [text[i : i + n] for i in range(0, len(text), n)]:
            irc_bridge.preactor.send_message(addr, target, fragment)


@simplebot.command
def me(payload: str, message: Message) -> None:
    """Send a message to IRC using the /me IRC command."""
    target = db.get_channel_by_gid(message.chat.id)
    if target:
        addr = message.get_sender_contact().addr
    else:
        pvchat = db.get_pvchat_by_gid(message.chat.id)
        if pvchat:
            target = pvchat["nick"]
            addr = pvchat["addr"]
    if target:
        text = " ".join(payload.split("\n"))
        irc_bridge.preactor.send_action(addr, target, text)


@simplebot.command
def topic(message: Message, replies: Replies) -> None:
    """Show IRC channel topic."""
    chan = db.get_channel_by_gid(message.chat.id)
    if not chan:
        replies.add(text="❌ This is not an IRC channel")
    else:
        replies.add(text=f"Topic:\n{irc_bridge.get_topic(chan)}")


@simplebot.command
def names(message: Message, replies: Replies) -> None:
    """Show list of IRC channel members."""
    chan = db.get_channel_by_gid(message.chat.id)
    if not chan:
        replies.add(text="❌ This is not an IRC channel")
        return

    html = "👥 Members: <ul>"
    count = 0
    for m in sorted(irc_bridge.get_members(chan)):
        html += f"<li>{m}</li>"
        count += 1
    html += "</ul>"

    replies.add(text=f"👥 Members ({count})", html=html)


@simplebot.command(name="/nick")
def nick_cmd(args: list, message: Message, replies: Replies) -> None:
    """Set your IRC nick or display your current nick if no new nick is given."""
    addr = message.get_sender_contact().addr
    if args:
        new_nick = "_".join(args)
        if not nick_re.match(new_nick):
            replies.add(
                text="❌ Invalid nick, only letters and numbers are"
                " allowed, and nick should be less than 30 characters"
            )
        elif db.get_addr(new_nick):
            replies.add(text="❌ Nick already taken")
        else:
            db.set_nick(addr, new_nick)
            irc_bridge.preactor.set_nick(addr, new_nick)
            replies.add(text=f"** Nick: {new_nick}")
    else:
        replies.add(text=f"** Nick: {db.get_nick(addr)}")


@simplebot.command
def query(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Open a private chat with an IRC user."""
    if not payload:
        replies.add(text="❌ Wrong syntax")
        return
    g = bot.get_chat(db.get_pvchat(message.get_sender_contact().addr, payload))
    replies.add(text=f"**Send messages to {payload} here.**", chat=g)


@simplebot.command
def join(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Join the given IRC channel."""
    sender = message.get_sender_contact()
    if not payload:
        replies.add(text="❌ Wrong syntax")
        return
    if not bot.is_admin(sender.addr) and not db.is_whitelisted(payload):
        replies.add(text="❌ That channel isn't in the whitelist")
        return

    g = bot.get_chat(db.get_chat(payload))
    if g and sender in g.get_contacts():
        replies.add(
            text=f"❌ {sender.addr}, you are already a member of this group", chat=g
        )
        return
    if g is None:
        chat = bot.create_group(payload, [sender])
        db.add_channel(payload, chat.id)
        irc_bridge.join_channel(payload)
        irc_bridge.preactor.join_channel(sender.addr, payload)
    else:
        _add_contact(g, sender)
        chat = bot.get_chat(sender)

    nick = db.get_nick(sender.addr)
    text = f"** You joined {payload} as {nick}"
    replies.add(text=text, chat=chat)


@simplebot.command
def remove(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Remove the member with the given nick from the IRC channel, if no nick is given remove yourself."""
    sender = message.get_sender_contact()

    channel = db.get_channel_by_gid(message.chat.id)
    if not channel:
        args = payload.split(maxsplit=1)
        channel = args[0]
        payload = args[1] if len(args) == 2 else ""
        g = bot.get_chat(db.get_chat(channel))
        if not g or sender not in g.get_contacts():
            replies.add(text="❌ You are not a member of that channel")
            return

    if not payload:
        payload = sender.addr
    if "@" not in payload:
        t = db.get_addr(payload)
        if not t:
            replies.add(text=f"❌ Unknow user: {payload}")
            return
        payload = t

    g = bot.get_chat(db.get_chat(channel))
    for c in g.get_contacts():
        if c.addr == payload:
            g.remove_contact(c)
            if c == sender:
                return
            s_nick = db.get_nick(sender.addr)
            nick = db.get_nick(c.addr)
            text = f"** {nick} removed by {s_nick}"
            bot.get_chat(db.get_chat(channel)).send_text(text)
            text = f"Removed from {channel} by {s_nick}"
            replies.add(text=text, chat=bot.get_chat(c))
            return


def _run_irc(bot: DeltaBot) -> None:
    while True:
        try:
            bot.logger.debug("[bot] Connecting...")
            irc_bridge.start()
        except Exception as ex:  # noqa
            bot.logger.exception("Error on IRC bridge: %s", ex)
            sleep(5)


def _getdefault(bot: DeltaBot, key: str, value: str = None) -> str:
    val = bot.get(key, scope=__name__)
    if val is None and value is not None:
        bot.set(key, value, scope=__name__)
        val = value
    return val


def _get_db(bot) -> DBManager:
    path = os.path.join(os.path.dirname(bot.account.db_path), __name__)
    if not os.path.exists(path):
        os.makedirs(path)
    return DBManager(bot, os.path.join(path, "sqlite.db"))


def _add_contact(chat: Chat, contact: Contact) -> None:
    img_path = chat.get_profile_image()
    if img_path and not os.path.exists(img_path):
        chat.remove_profile_image()
    chat.add_contact(contact)


def _upload(filename: str, file: IO, url: str) -> str:
    try:
        with session.post(url, files=dict(file=(filename, file))) as resp:
            resp.raise_for_status()
            return resp.text.strip()
    except requests.RequestException:
        return ""
