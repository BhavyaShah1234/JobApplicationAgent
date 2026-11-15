"""F02: cleaning up a used verification mail must never delete on a failed COPY, nor expunge other messages."""
import imaplib
from datetime import UTC, datetime

import pytest

from jobagent import db
from jobagent.tools.inbox import Inbox, Mail

TRASH = '"[Gmail]/Trash"'


class Mailbox:
    """A strict fake IMAP session: folders of {uid: flags}, capabilities, and per-command failures. A plain EXPUNGE
    removes EVERY message flagged \\Deleted in the selected folder; `UID EXPUNGE n` removes only n."""

    def __init__(self, inbox_uids=("5",), deleted=(), caps=("IMAP4REV1", "MOVE", "UIDPLUS"), fail=(), raise_on=()):
        self.folders = {"INBOX": {u: set() for u in inbox_uids}, TRASH: {}}
        for u in deleted:
            self.folders["INBOX"][u] = {"\\Deleted"}          # somebody else's message, already flagged
        self.capabilities = tuple(caps)
        self.fail, self.raise_on, self.log, self.selected = set(fail), set(raise_on), [], "INBOX"

    def select(self, folder):
        self.selected = folder
        return "OK", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"']

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        self.log.append((cmd, *args))
        if cmd in self.raise_on:
            raise imaplib.IMAP4.error(f"{cmd} not supported")
        if cmd in self.fail or (cmd == "MOVE" and "MOVE" not in self.capabilities):
            return "NO", [b"refused"]
        box = self.folders[self.selected]
        if cmd == "MOVE":
            if args[0] not in box:
                return "NO", [b"no such message"]
            self.folders[args[1]][args[0]] = box.pop(args[0])
            return "OK", [None]
        if cmd == "COPY":
            if args[0] not in box:
                return "NO", [b"no such message"]
            self.folders[args[1]][args[0]] = set(box[args[0]])
            return "OK", [None]
        if cmd == "STORE":
            if args[0] not in box:
                return "NO", [b"no such message"]
            box[args[0]].add("\\Deleted")
            return "OK", [None]
        if cmd == "EXPUNGE":                                  # UID EXPUNGE <uid>
            if "UIDPLUS" not in self.capabilities:
                return "BAD", [b"UIDPLUS not supported"]
            if "\\Deleted" in box.get(args[0], set()):
                box.pop(args[0])
            return "OK", [None]
        if cmd == "SEARCH":
            return "OK", [" ".join(box).encode()]
        raise AssertionError(cmd)

    def expunge(self):
        self.log.append(("EXPUNGE",))
        box = self.folders[self.selected]
        for u in [u for u, flags in box.items() if "\\Deleted" in flags]:
            box.pop(u)
        return "OK", [None]

    def commands(self):
        return [c[0] for c in self.log]


def make(tmp_path, mailbox):
    conn = db.connect(tmp_path / "jobs.sqlite")
    db.record_agent_domain(conn, "https://boards.greenhouse.io/acme")
    inbox = Inbox("agent@gmail.com", "abcd efgh ijkl mnop", conn)
    inbox._imap = mailbox
    mail = Mail("5", "<code5@x>", "no-reply@us.greenhouse-mail.io", "Security code for your application",
                datetime.now(UTC), "Your code is 483920")
    inbox.begin_action("https://boards.greenhouse.io/acme", "portal")          # an agent action consumed this very mail
    with conn:
        conn.execute("UPDATE agent_actions SET status = 'consumed', consumed_message_id = ?", (mail.message_id,))
    return inbox, mail, conn


def audit(conn):
    return [dict(r) for r in conn.execute("SELECT message_id, purpose, status FROM verification_log")]


# --- the counterexamples (state only: these fail on the old code whatever it returns) --------------------------------

def test_a_failed_copy_leaves_the_original_untouched(tmp_path):
    box = Mailbox(fail={"MOVE", "COPY"})
    inbox, mail, _ = make(tmp_path, box)
    inbox.consume_and_trash(mail, "otp")
    assert "STORE" not in box.commands() and "EXPUNGE" not in box.commands()
    assert box.folders["INBOX"]["5"] == set() and box.folders[TRASH] == {}


def test_another_deleted_message_survives_the_cleanup_of_an_agent_message(tmp_path):
    box = Mailbox(inbox_uids=("5", "9"), deleted=("9",), caps=("IMAP4REV1", "UIDPLUS"))      # no MOVE: the fallback runs
    inbox, mail, _ = make(tmp_path, box)
    inbox.consume_and_trash(mail, "otp")
    assert "9" in box.folders["INBOX"], "the user's own flagged message was expunged"
    assert "5" not in box.folders["INBOX"] and "5" in box.folders[TRASH]


def test_without_uid_expunge_nothing_is_copied_or_deleted(tmp_path):
    box = Mailbox(inbox_uids=("5", "9"), deleted=("9",), caps=("IMAP4REV1",))                # no MOVE, no UIDPLUS
    inbox, mail, _ = make(tmp_path, box)
    inbox.consume_and_trash(mail, "otp")
    assert box.commands() == ["MOVE"] and set(box.folders["INBOX"]) == {"5", "9"} and box.folders[TRASH] == {}


# --- the behaviour that must remain ---------------------------------------------------------------------------------

def test_a_successful_move_is_the_whole_cleanup(tmp_path):
    box = Mailbox(inbox_uids=("5", "9"), deleted=("9",))
    inbox, mail, conn = make(tmp_path, box)
    res = inbox.consume_and_trash(mail, "otp")
    assert res.ok and res.status == "completed" and box.commands() == ["MOVE"]
    assert "9" in box.folders["INBOX"] and "5" in box.folders[TRASH]
    assert audit(conn) == [{"message_id": "<code5@x>", "purpose": "otp", "status": "completed"}]


def test_the_fallback_copies_flags_and_expunges_only_that_uid(tmp_path):
    box = Mailbox(caps=("IMAP4REV1", "UIDPLUS"))
    inbox, mail, conn = make(tmp_path, box)
    res = inbox.consume_and_trash(mail, "otp")
    assert res.ok and box.commands() == ["MOVE", "COPY", "STORE", "EXPUNGE"]
    assert ("EXPUNGE", "5") in box.log and ("EXPUNGE",) not in box.log                       # UID EXPUNGE 5, never the plain one
    assert audit(conn)[0]["status"] == "completed"


def test_a_move_that_raises_falls_back_the_same_way(tmp_path):
    box = Mailbox(raise_on={"MOVE"})
    inbox, mail, _ = make(tmp_path, box)
    assert inbox.consume_and_trash(mail, "otp").ok and "5" in box.folders[TRASH] and "5" not in box.folders["INBOX"]


def test_a_failed_store_after_a_good_copy_does_not_expunge_and_is_reported(tmp_path):
    box = Mailbox(fail={"MOVE", "STORE"})
    inbox, mail, conn = make(tmp_path, box)
    res = inbox.consume_and_trash(mail, "otp")
    assert not res.ok and res.status == "failed" and "STORE" in res.detail
    assert "EXPUNGE" not in box.commands() and "5" in box.folders["INBOX"]
    assert audit(conn)[0]["status"] == "failed"                                              # the attempt, never a success


def test_the_audit_row_says_completed_only_after_it_happened(tmp_path):
    box = Mailbox(fail={"MOVE", "COPY"})
    inbox, mail, conn = make(tmp_path, box)
    res = inbox.consume_and_trash(mail, "otp")
    assert not res.ok and [r["status"] for r in audit(conn)] == ["failed"]
    unsupported = Mailbox(caps=("IMAP4REV1",))
    second = tmp_path / "b"
    second.mkdir()
    inbox2, mail2, conn2 = make(second, unsupported)
    res2 = inbox2.consume_and_trash(mail2, "otp")
    assert res2.status == "unsupported" and "UIDPLUS" in res2.detail and [r["status"] for r in audit(conn2)] == ["failed"]


def test_cleaning_up_a_message_that_is_already_gone_deletes_nothing_else(tmp_path):
    box = Mailbox(inbox_uids=("9",), deleted=("9",), caps=("IMAP4REV1", "UIDPLUS"))
    inbox, mail, _ = make(tmp_path, box)                                   # uid 5 is not in INBOX any more
    res = inbox.consume_and_trash(mail, "otp")
    assert not res.ok and set(box.folders["INBOX"]) == {"9"} and "EXPUNGE" not in box.commands()


def test_the_ownership_and_protection_checks_still_refuse_first(tmp_path):
    box = Mailbox()
    inbox, mail, conn = make(tmp_path, box)
    other = Mail("6", "<o@x>", "friend@example.com", "Hi", datetime.now(UTC), "hello")
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(other, "otp")
    protected = Mail("5", "<code5@x>", "no-reply@us.greenhouse-mail.io", "Your interview", datetime.now(UTC), "schedule an interview")
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(protected, "otp")
    assert box.log == [] and audit(conn) == []


def test_the_sweep_counts_only_messages_that_were_really_cleaned(tmp_path):
    from datetime import timedelta
    box = Mailbox(fail={"MOVE", "COPY"})
    inbox, mail, _ = make(tmp_path, box)
    old = Mail("5", "<code5@x>", mail.sender, f"Your verification code jobagent-selftest-x", datetime.now(UTC) - timedelta(hours=48),
               mail.text)
    inbox.fetch_headers = lambda uid, folder="INBOX": old                   # an old, tagged self-test (the agent's own)
    inbox.fetch_text = lambda m, folder="INBOX": old
    assert inbox.sweep_stale_verification(max_age_h=24) == []              # the cleanup failed: nothing reported as trashed
