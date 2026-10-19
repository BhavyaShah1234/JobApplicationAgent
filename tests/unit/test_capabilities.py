from jobagent.tools.capabilities import HEADER, report_missing_capability


def test_reports_once(tmp_path):
    path = tmp_path / "missing-agent-capabilities.txt"
    assert report_missing_capability("ffmpeg", "convert a video JD", "apt-get install ffmpeg", path)
    assert not report_missing_capability("ffmpeg", "again", "", path)
    text = path.read_text()
    assert text.startswith(HEADER)
    assert text.count("| ffmpeg |") == 1


def test_handled_entries_can_be_reported_again(tmp_path):
    path = tmp_path / "caps.txt"
    report_missing_capability("ffmpeg", "x", "", path)
    path.write_text(path.read_text().replace("\n2", "\n# done 2"))
    assert report_missing_capability("ffmpeg", "needed again", "", path)


def test_pipes_and_newlines_are_sanitized(tmp_path):
    path = tmp_path / "caps.txt"
    report_missing_capability("pkg", "a | b\nc", "", path)
    line = path.read_text().splitlines()[-1]
    assert line.count(" | ") == 3
