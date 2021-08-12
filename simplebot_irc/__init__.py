import os
import re
from threading import Thread
from time import sleep

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
db: DBManager
irc_bridge: IRCBot


@simplebot.hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    _getdefault(bot, "nick", "DC-Bridge")
    _getdefault(bot, "host", "irc.libera.chat")
    _getdefault(bot, "port", "6667")


@simplebot.hookimpl
def deltabot_start(bot: DeltaBot) -> None:
    global db, irc_bridge
    db = _get_db(bot)
    nick = _getdefault(bot, "nick")
    host = _getdefault(bot, "host")
    port = int(_getdefault(bot, "port"))
    irc_bridge = IRCBot(host, port, nick, db, bot)
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


@simplebot.filter(name=__name__)
def filter_messages(bot: DeltaBot, message: Message) -> None:
    """Process messages sent to an IRC channel."""
    target = db.get_channel_by_gid(message.chat.id)
    if target:
        addr = message.get_sender_contact().addr
    else:
        pvchat = db.get_pvchat_by_gid(message.chat.id)
        if pvchat:
            target = pvchat["nick"]
            addr = pvchat["addr"]
    if target:
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
            text += "[File] "
        text += message.text
        if not text:
            return

        text = " ".join(text.split("\n"))
        n = 450
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
        replies.add(text="This is not an IRC channel")
    else:
        replies.add(text="Topic:\n{}".format(irc_bridge.get_topic(chan)))


@simplebot.command
def names(message: Message, replies: Replies) -> None:
    """Show list of IRC channel members."""
    chan = db.get_channel_by_gid(message.chat.id)
    if not chan:
        replies.add(text="This is not an IRC channel")
        return

    members = "Members:\n"
    for m in sorted(irc_bridge.get_members(chan)):
        members += "• {}\n".format(m)

    replies.add(text=members)


@simplebot.command(name="/nick")
def nick_cmd(args: list, message: Message, replies: Replies) -> None:
    """Set your IRC nick or display your current nick if no new nick is given."""
    addr = message.get_sender_contact().addr
    if args:
        new_nick = "_".join(args)
        if not nick_re.match(new_nick):
            replies.add(
                text="** Invalid nick, only letters and numbers are"
                " allowed, and nick should be less than 30 characters"
            )
        elif db.get_addr(new_nick):
            replies.add(text="** Nick already taken")
        else:
            db.set_nick(addr, new_nick)
            irc_bridge.preactor.set_nick(addr, new_nick)
            replies.add(text="** Nick: {}".format(new_nick))
    else:
        replies.add(text="** Nick: {}".format(db.get_nick(addr)))


@simplebot.command
def query(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Open a private chat with an IRC user."""
    if not payload:
        replies.add(text="Wrong syntax")
        return
    g = bot.get_chat(db.get_pvchat(message.get_sender_contact().addr, payload))
    replies.add(text=f"**Send messages to {payload} here.**", chat=g)


@simplebot.command
def join(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Join the given IRC channel."""
    sender = message.get_sender_contact()
    if not payload:
        replies.add(text="Wrong syntax")
        return
    if not bot.is_admin(sender.addr) and not db.is_whitelisted(payload):
        replies.add(text="That channel isn't in the whitelist")
        return

    g = bot.get_chat(db.get_chat(payload))
    if g and sender in g.get_contacts():
        replies.add(text="You are already a member of this group", chat=g)
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
    text = "** You joined {} as {}".format(payload, nick)
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
            replies.add(text="You are not a member of that channel")
            return

    if not payload:
        payload = sender.addr
    if "@" not in payload:
        t = db.get_addr(payload)
        if not t:
            replies.add(text="Unknow user: {}".format(payload))
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
            text = "** {} removed by {}".format(nick, s_nick)
            bot.get_chat(db.get_chat(channel)).send_text(text)
            text = "Removed from {} by {}".format(channel, s_nick)
            replies.add(text=text, chat=bot.get_chat(c))
            return


def _run_irc(bot: DeltaBot) -> None:
    while True:
        try:
            irc_bridge.start()
        except Exception as ex:
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
