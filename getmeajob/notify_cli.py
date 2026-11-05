"""`notify` commands: set up Gmail, preview digests locally, send them. Nothing is ever applied to."""
import argparse
from pathlib import Path
import subprocess

from getmeajob import notify
from getmeajob.config import ConfigurationError
from getmeajob.resume.store import ProfileStore

PRIVATE = Path("private")


def configure(commands):
    group = commands.add_parser("notify", help="Email-alert presets: new best-matching jobs after each refresh")
    group.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    actions = group.add_subparsers(dest="notify_action", required=True)
    setup = actions.add_parser("setup", help="Store your Gmail app password in macOS Keychain (prompted, never shown)")
    setup.add_argument("--address", required=True, help="Your own @gmail.com address")
    actions.add_parser("presets", help="List saved presets")
    for name, text in (("preview", "Write digests as local HTML files; send nothing and mark nothing"),
                       ("send", "Email one digest per preset with new postings"),
                       ("test", "Send a short test email to yourself")):
        action = actions.add_parser(name, help=text)
        if name != "test":
            action.add_argument("--preset", type=int, help="Only this preset id")
    return group


def explain(exc: Exception, address: str) -> str:
    """Plain-language reason for a failed send. Never includes the password."""
    name = type(exc).__name__
    if name == "SMTPAuthenticationError":
        return "Gmail rejected the login. Check the App Password (re-run notify setup) and that 2-Step Verification is on."
    if name in ("SMTPServerDisconnected", "SMTPConnectError"):
        return (f"Gmail closed the connection. The most common cause is a wrong saved address (currently {address[:2]}***{address[address.index('@'):]}); "
                "re-run notify setup with your real Gmail address. A school or campus network blocking mail can also cause this.")
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"Could not send ({name}). Check your internet connection and run notify setup again if it keeps failing."


def run(args, settings, sender_factory=notify.gmail_sender, store=None) -> int:
    store = store or ProfileStore(PRIVATE)
    action = args.notify_action
    if action == "setup":
        if not notify.valid_address(args.address):
            raise ConfigurationError("Use your own real @gmail.com address, for example jane.smith@gmail.com (not the example text).")
        print("Create a Gmail App Password first (Google Account > Security > 2-Step Verification > App passwords).")
        print("macOS will now ask for it. It goes straight into Keychain and is not shown or saved anywhere else.")
        done = subprocess.run(["security", "add-generic-password", "-U", "-a", args.address, "-s", notify.KEYCHAIN_SERVICE, "-w"])
        if done.returncode != 0:
            raise ConfigurationError("Keychain did not save the password.")
        store.set_setting("notify_address", args.address)
        print(f"Saved. Next: .venv/bin/python -m getmeajob notify test")
        return 0
    if action == "presets":
        for preset in store.presets():
            print(f"{preset['id']}: {preset['name']} | {'on' if preset['enabled'] else 'off'} | {preset['sent']} emailed | last run {preset['last_run'] or 'never'}")
        if not store.presets():
            print("No presets yet. Open Matching jobs in the browser, set filters and use 'Save as email preset'.")
        return 0
    if action in ("preview", "send"):
        digests = notify.build_digests(settings.database, store, only=args.preset)
        for digest in digests:
            note = f" ({'; '.join(digest.notes)})" if digest.notes else ""
            print(f"{digest.preset['name']}: {len(digest.items)} new match(es) to include, {digest.total_new} matching in total{note}")
        if action == "preview":
            for path in notify.write_previews(digests, PRIVATE / "digests"):
                print(f"Preview written: {path}")
            print("Nothing was sent or marked as sent.")
            return 0
        address = store.get_setting("notify_address")
        if not address:
            raise ConfigurationError("Email is not set up. Run: .venv/bin/python -m getmeajob notify setup --address YOU@gmail.com")
        report = notify.send_digests(store, digests, sender_factory(address))
        print(f"Emails sent: {report['emails']} ({report['postings']} posting(s)).")
        for error in report["errors"]:
            print(f"Not sent, will retry next time: {error}")
        return 1 if report["errors"] else 0
    if action == "test":
        address = store.get_setting("notify_address")
        if not address:
            raise ConfigurationError("Email is not set up. Run: .venv/bin/python -m getmeajob notify setup --address YOU@gmail.com")
        try:
            sender_factory(address)("GetMeAJob test email", "Email alerts are working. This message contains no job or resume data.")
        except Exception as exc:
            raise ConfigurationError(explain(exc, address)) from None
        print("Test email sent to yourself.")
        return 0
    return 2


def after_search(settings, sender_factory=notify.gmail_sender, store=None) -> None:
    """Called by `search --notify`. Failures are reported but never fail or undo the search."""
    store = store or ProfileStore(PRIVATE)
    address = store.get_setting("notify_address")
    if not address:
        print("Notifications skipped: email is not set up (notify setup).")
        return
    try:
        digests = notify.build_digests(settings.database, store)
        report = notify.send_digests(store, digests, sender_factory(address))
        print(f"Notifications: {report['emails']} email(s), {report['postings']} posting(s).")
        for error in report["errors"]:
            print(f"Notification not sent, will retry next time: {error}")
    except Exception as exc:
        print(f"Notifications skipped: {type(exc).__name__}")
