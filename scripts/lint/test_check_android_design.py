"""Tests for the Android design-token check (scripts/lint/check_android_design.py)."""

from pathlib import Path

import check_android_design as chk


def scan(tmp_path: Path, rel: str, text: str) -> list[str]:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return [v.rule for v in chk.check_file(f, tmp_path)]


RES = "android/app/src/main/res"
SRC = "android/app/src/main/java/x"


def test_layout_hex_colour_flagged(tmp_path):
    assert scan(tmp_path, f"{RES}/layout/a.xml", '<View android:background="#00F5D4"/>') == ["colour"]


def test_layout_literal_dimen_and_text_size_flagged(tmp_path):
    xml = '<View android:layout_width="36dp" android:textSize="13sp"/>'
    assert scan(tmp_path, f"{RES}/layout-w840dp/a.xml", xml) == ["dimen", "dimen"]


def test_layout_tokens_and_zero_dp_allowed(tmp_path):
    xml = '<View android:layout_width="0dp" android:padding="@dimen/inst_space_8" android:background="@color/inst_bg"/>'
    assert scan(tmp_path, f"{RES}/layout/a.xml", xml) == []


def test_drawable_hex_flagged_but_dp_allowed(tmp_path):
    xml = '<shape><solid android:color="#00F5D4"/><corners android:radius="10dp"/></shape>'
    assert scan(tmp_path, f"{RES}/drawable/a.xml", xml) == ["colour"]


def test_icon_mask_white_allowed(tmp_path):
    xml = '<vector><path android:fillColor="#FFFFFF" android:pathData="M0,0"/></vector>'
    assert scan(tmp_path, f"{RES}/drawable/ic_x.xml", xml) == []


def test_non_white_icon_fill_flagged(tmp_path):
    xml = '<vector><path android:fillColor="#FF0000" android:pathData="M0,0"/></vector>'
    assert scan(tmp_path, f"{RES}/drawable/ic_x.xml", xml) == ["colour"]


def test_values_colour_flagged_dimens_file_allowed(tmp_path):
    assert scan(tmp_path, f"{RES}/values/colors.xml", '<color name="a">#0D1117</color>') == ["colour"]
    assert scan(tmp_path, f"{RES}/values/dimens.xml", '<dimen name="a">40sp</dimen>') == []


def test_ignore_marker_same_or_previous_line(tmp_path):
    same = '<color name="a">#0D1117</color> <!-- design-lint:ignore launcher tile -->'
    prev = '<!-- design-lint:ignore launcher tile -->\n<color name="a">#0D1117</color>'
    assert scan(tmp_path, f"{RES}/values/colors.xml", same) == []
    assert scan(tmp_path, f"{RES}/values/colors2.xml", prev) == []


def test_vendored_instrument_is_skipped(tmp_path):
    assert scan(tmp_path, "android/app/src/instrument/android/res/values/c.xml", '<color name="a">#fff</color>') == []


def test_kotlin_colour_and_size_literals_flagged(tmp_path):
    kt = "val a = Color(0xFF00F5D4)\nval b = Color.Red\nval c = 14.sp\nval d = 0.dp\n"
    assert scan(tmp_path, f"{SRC}/A.kt", kt) == ["colour", "colour", "dimen"]


def test_main_walks_the_repo(tmp_path, capsys):
    (tmp_path / f"{RES}/layout").mkdir(parents=True)
    (tmp_path / f"{RES}/layout/a.xml").write_text('<View android:layout_width="36dp"/>')
    assert chk.main([str(tmp_path)]) == 1
    assert "a.xml:1" in capsys.readouterr().out
    (tmp_path / f"{RES}/layout/a.xml").write_text('<View android:layout_width="@dimen/x"/>')
    assert chk.main([str(tmp_path)]) == 0
