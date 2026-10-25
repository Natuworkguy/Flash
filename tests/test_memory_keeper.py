# pylint: disable=C0114,C0115,C0116

from flash import memory, sparks


def test_memory_knows_when_each_was_added_and_recalled():
    memory.add_memory("Lives in Toronto")
    memory.add_memory("Likes tea")
    memory.search_memory("tea")
    memory.search_memory("tea")

    toronto, tea = memory.usage()
    assert toronto["added"] > 0 and toronto["recalled"] == 0  # nosec B101
    assert tea["recalled"] == 2 and tea["last"] > 0  # nosec B101


def test_merging_keeps_the_oldest_and_every_recall():
    memory.add_memory("Likes tea")
    memory.add_memory("Lives in Toronto")
    memory.add_memory("Drinks green tea")
    memory.search_memory("tea")

    said = memory.merge_memories([1, 3], "[Preference] Likes green tea")
    assert said.startswith("Merged 2")  # nosec B101
    assert memory.list_memory() == [  # nosec B101
        "[Preference] Likes green tea", "Lives in Toronto"]
    assert memory.usage()[0]["recalled"] == 2  # nosec B101


def test_the_memory_keeper_tidies_and_backs_up_first(tmp_path):
    memory.add_memory("Likes tea")
    memory.add_memory("likes tea a lot")
    keeper = sparks.add_from("memory keeper")
    assert keeper.at and sparks.tidies_memory(keeper)  # nosec B101

    tidy = sparks._tidying(keeper)
    listed = tidy({"action": "list"})
    assert "1. Likes tea  (added" in listed  # nosec B101
    assert "never recalled" in listed  # nosec B101

    said = tidy({"action": "merge", "indexes": [1, 2],
                 "text": "[Preference] Likes tea a lot"})
    assert "Now:\n1. [Preference] Likes tea a lot" in said  # nosec B101
    backups = list((memory.MEMORY_PATH.parent / ".flash"
                    / "memory-backups").glob("*.md"))
    assert len(backups) == 1  # nosec B101
    assert "likes tea a lot" in backups[0].read_text()  # nosec B101

    assert tidy({"action": "forget", "index": 1}).startswith(  # nosec B101
        "Error: say why")
    assert tidy({"action": "edit", "index": 9, "text": "x"}).startswith(
        "Error")  # nosec B101
