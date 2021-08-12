import sqlite3
import string
from typing import Generator, Optional


class DBManager:
    def __init__(self, bot, db_path: str) -> None:
        self.bot = bot
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS channels
                (name TEXT PRIMARY KEY, chat INTEGER)"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS pvchats
                (addr TEXT, nick TEXT, chat INTEGER,
                PRIMARY KEY(addr, nick))"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS nicks
                (addr TEXT PRIMARY KEY,
                nick TEXT NOT NULL)"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS whitelist
                (channel TEXT PRIMARY KEY)"""
            )

        for addr, nick in self.db.execute("SELECT addr, nick from nicks"):
            if nick != nick.rstrip("_"):
                self.set_nick(addr, nick.rstrip("_"))

    def execute(self, statement: str, args=()) -> sqlite3.Cursor:
        return self.db.execute(statement, args)

    def commit(self, statement: str, args=()) -> sqlite3.Cursor:
        with self.db:
            return self.db.execute(statement, args)

    def close(self) -> None:
        self.db.close()

    # ==== pvchats =====

    def get_pvchat(self, addr: str, nick: str) -> int:
        r = self.execute(
            "SELECT chat FROM pvchats WHERE addr=? AND nick=?", (addr, nick)
        ).fetchone()
        if r:
            return r[0]
        chat = self.bot.create_group(nick + " [irc]", [addr])
        self.commit("INSERT INTO pvchats VALUES (?,?,?)", (addr, nick, chat.id))
        return chat.id

    def get_pvchat_by_gid(self, gid: int) -> Optional[sqlite3.Row]:
        r = self.execute("SELECT * FROM pvchats WHERE chat=?", (gid,)).fetchone()
        return r

    def remove_pvchat(self, addr: str, nick: str) -> None:
        self.commit("DELETE FROM pvchats WHERE addr=? AND nick=?", (addr, nick))

    # ==== channels =====

    def get_chat(self, name: str) -> Optional[int]:
        name = name.lower()
        r = self.execute("SELECT chat FROM channels WHERE name=?", (name,)).fetchone()
        return r and r[0]

    def get_channel_by_gid(self, gid: int) -> Optional[str]:
        r = self.db.execute("SELECT name from channels WHERE chat=?", (gid,)).fetchone()
        return r and r[0]

    def get_channels(self) -> Generator:
        for r in self.db.execute("SELECT * FROM channels"):
            yield r

    def add_channel(self, name: str, chat: int) -> None:
        self.commit("INSERT INTO channels VALUES (?,?)", (name.lower(), chat))

    def remove_channel(self, name: str) -> None:
        self.commit("DELETE FROM channels WHERE name=?", (name.lower(),))

    # ===== nicks =======

    def get_nick(self, addr: str) -> str:
        r = self.execute("SELECT nick from nicks WHERE addr=?", (addr,)).fetchone()
        if r:
            return r[0]
        allowed = string.ascii_letters + string.digits + r"_-\[]{}^`|"
        name = self.bot.get_contact(addr).name
        name = "".join(list(filter(allowed.__contains__, name)))[:13]
        nick = name
        i = 2
        while True:
            if not self.get_addr(nick):
                self.set_nick(addr, nick)
                break
            nick = f"{name}{i}"
            if len(nick) > 13:
                nick = name[: len(name) - 1]
            i += 1
        return nick

    def set_nick(self, addr: str, nick: str) -> None:
        self.commit("REPLACE INTO nicks VALUES (?,?)", (addr, nick))

    def get_addr(self, nick: str) -> str:
        r = self.execute("SELECT addr FROM nicks WHERE nick=?", (nick,)).fetchone()
        return r and r[0]

    # ===== whitelist =======

    def is_whitelisted(self, name: str) -> bool:
        rows = self.execute("SELECT channel FROM whitelist").fetchall()
        if not rows:
            return True
        for r in rows:
            if r[0] == name:
                return True
        return False

    def add_to_whitelist(self, name: str) -> None:
        self.commit("INSERT INTO whitelist VALUES (?)", (name,))

    def remove_from_whitelist(self, name: str) -> None:
        self.commit("DELETE FROM whitelist WHERE id=?", (name,))
