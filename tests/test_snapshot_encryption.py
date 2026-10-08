"""An owner may derive once while every saved ciphertext remains independent."""

from secure_mcp import encrypted_session
from secure_mcp.encrypted_session import SnapshotEncryption, load_session, save_session
from secure_mcp.session import PrivacySession


def test_encryption_context_derives_once_and_snapshots_decrypt_after_restart(
    tmp_path, monkeypatch
):
    original_cipher = encrypted_session._cipher
    derivations = []

    def derive(password, salt):
        derivations.append(salt)
        return original_cipher(password, salt)

    monkeypatch.setattr(encrypted_session, "_cipher", derive)
    context = SnapshotEncryption("offline-snapshot-password")
    session = PrivacySession("owner")
    path = tmp_path / "owner.enc"
    save_session(path, session, "offline-snapshot-password", encryption=context)
    first = path.read_bytes()
    save_session(path, session, "offline-snapshot-password", encryption=context)
    assert len(derivations) == 1
    assert path.read_bytes() != first
    assert (
        load_session(path, "offline-snapshot-password", "owner").session_id == "owner"
    )


def test_separate_owners_and_default_writes_have_independent_salts(
    tmp_path, monkeypatch
):
    original_cipher = encrypted_session._cipher
    derivations = []

    def derive(password, salt):
        derivations.append(salt)
        return original_cipher(password, salt)

    monkeypatch.setattr(encrypted_session, "_cipher", derive)
    first = SnapshotEncryption("offline-snapshot-password")
    second = SnapshotEncryption("offline-snapshot-password")
    session = PrivacySession("owner")
    path = tmp_path / "owner.enc"
    for context in (first, second, None, None):
        save_session(path, session, "offline-snapshot-password", encryption=context)
    assert len(derivations) == len(set(derivations)) == 4
