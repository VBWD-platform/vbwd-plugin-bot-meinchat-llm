"""S98.1 — text chunker unit tests."""
from plugins.bot_meinchat_llm.bot_meinchat_llm.services.text_chunker import chunk_text


def test_blank_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_a_single_chunk():
    chunks = chunk_text("hello world", chunk_size_tokens=100, overlap_tokens=10)
    assert chunks == ["hello world"]


def test_long_text_splits_with_overlap():
    words = " ".join(str(n) for n in range(20))
    chunks = chunk_text(words, chunk_size_tokens=8, overlap_tokens=3)
    # step = 8 - 3 = 5 → starts at 0, 5, 10, 15 → 4 chunks
    assert len(chunks) == 4
    # Overlap: the tail of chunk 0 reappears at the head of chunk 1.
    first_tail = chunks[0].split()[-3:]
    second_head = chunks[1].split()[:3]
    assert first_tail == second_head


def test_overlap_clamped_below_chunk_size_advances():
    words = " ".join(str(n) for n in range(10))
    # overlap >= size would stall; the chunker clamps it so the window advances.
    chunks = chunk_text(words, chunk_size_tokens=4, overlap_tokens=10)
    assert len(chunks) >= 1
    assert all(chunk for chunk in chunks)
