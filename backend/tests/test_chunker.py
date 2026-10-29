from app.ingestion.chunker import chunk_markdown, count_tokens, split_sections


def test_splits_on_headings_and_keeps_heading_with_content() -> None:
    body = "# T\nintro\n\n## A\nalpha text\n\n## B\nbeta text"
    sections = split_sections(body)
    assert len(sections) == 3
    assert sections[1].startswith("## A") and "alpha" in sections[1]


def test_small_sections_are_packed_into_one_chunk() -> None:
    chunks = chunk_markdown("# T\nintro\n\n## A\nalpha\n\n## B\nbeta")
    assert len(chunks) == 1
    assert [c.seq for c in chunks] == [0]


def test_sections_are_not_merged_past_the_target() -> None:
    sec = "## S\n" + "word " * 120
    body = "\n\n".join(sec.replace("S", str(i)) for i in range(6))
    chunks = chunk_markdown(body, target=300, overlap=30)
    assert len(chunks) > 1
    assert all(c.token_count <= 300 for c in chunks)
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_long_section_is_windowed_with_overlap() -> None:
    body = "## Long\n" + " ".join(f"w{i}" for i in range(1200))
    chunks = chunk_markdown(body, target=200, overlap=40)
    assert len(chunks) > 3
    assert all(c.token_count <= 205 for c in chunks)
    # consecutive windows share text
    tail = chunks[0].text.split()[-5:]
    assert all(w in chunks[1].text for w in tail)


def test_default_sizes_follow_spec() -> None:
    body = "## Long\n" + "alpha beta gamma delta " * 400
    chunks = chunk_markdown(body)
    assert max(c.token_count for c in chunks) <= 500
    assert count_tokens(chunks[0].text) == chunks[0].token_count


def test_empty_body_has_no_chunks() -> None:
    assert chunk_markdown("  \n") == []
