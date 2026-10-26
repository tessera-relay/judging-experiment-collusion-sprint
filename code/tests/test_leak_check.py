"""The e-mail check (O37) finds addresses in agent text and never prints them."""
import json

from collusion_study.leak_check import main, scan


def test_addresses_are_found_and_not_printed(tmp_path, capsys):
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "calls.jsonl").write_text(json.dumps({"text": "write to someone@example.org"}) + "\n")
    (ep / "colosseum.log").write_text("other@example.org and third@example.org")  # every text file is scanned
    (ep / "image.png").write_bytes(b"\x89PNG\0\0 x@example.org")  # binary files are not
    assert scan(tmp_path) == {"ep/calls.jsonl": 1, "ep/colosseum.log": 2}
    assert main([str(tmp_path)]) == 1
    assert "example.org" not in capsys.readouterr().out
