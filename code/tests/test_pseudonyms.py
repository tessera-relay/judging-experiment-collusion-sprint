"""Neutral agent names for the coders and scorers (O47)."""
from collusion_study.pseudonyms import hide, hide_messages, pseudonyms, reveal, reveal_text


def test_letters_are_seeded_per_episode_and_cover_every_agent():
    names = ["Maren", "Teodor", "Lucia", "Bram", "Ines", "Oskar"]
    a = pseudonyms(names, "channel_only-seed1")
    assert a == pseudonyms(list(reversed(names)), "channel_only-seed1")
    assert sorted(a.values()) == [f"Agent {c}" for c in "ABCDEF"]
    assert any(pseudonyms(names, f"ep{i}") != a for i in range(5))


def test_names_are_hidden_in_authors_and_text_and_mapped_back():
    m = pseudonyms(["Maren", "Mare"], "x")
    msgs = hide_messages([{"agent": "Maren", "text": "Mare, Maren's list is fine. Marengo stays."}], m)
    assert msgs[0]["agent"] == m["Maren"]
    assert msgs[0]["text"] == f"{m['Mare']}, {m['Maren']}'s list is fine. Marengo stays."
    assert reveal(m["Maren"], m) == "Maren" and reveal(m["Maren"][-1], m) == "Maren" and reveal("Nobody", m) == "Nobody"
    assert reveal_text(f"{m['Mare']} agreed", m) == "Mare agreed"
    assert hide("no names", {}) == "no names"
