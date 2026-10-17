# -*- coding: utf-8 -*-
"""退出码与确定性写盘的契约测试。"""

import json
import os

import pytest

from devdisk import exit_codes
from devdisk.serialize import dumps_json, write_json_atomic


def test_exit_codes_are_distinct_and_documented():
    values = [exit_codes.OK, exit_codes.REFUSED, exit_codes.USAGE,
              exit_codes.NOT_READY, exit_codes.DEGRADED, exit_codes.UNSAFE]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
    for value in values:
        assert value in exit_codes.NAMES
        assert value in exit_codes.DESCRIPTIONS


def test_dumps_is_deterministic_and_key_sorted():
    payload = {"b": 2, "a": {"z": [1, 2], "y": "中文"}}
    first = dumps_json(payload)
    second = dumps_json(json.loads(first))
    assert first == second
    assert first.endswith("\n")
    assert "\r" not in first
    assert first.index('"a"') < first.index('"b"')
    assert "中文" in first  # ensure_ascii=False


def test_write_json_atomic_roundtrip(tmp_path):
    target = tmp_path / "nested" / "out.json"
    payload = {"schema_version": 1, "items": [{"bytes": 1}]}
    write_json_atomic(str(target), payload)

    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8")) == payload


def test_write_json_atomic_replaces_existing(tmp_path):
    target = tmp_path / "out.json"
    write_json_atomic(str(target), {"v": 1})
    write_json_atomic(str(target), {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8"))["v"] == 2


def test_write_json_atomic_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out.json"
    write_json_atomic(str(target), {"v": 1})
    leftovers = [name for name in os.listdir(str(tmp_path)) if name.endswith(".tmp")]
    assert leftovers == []


def test_write_json_atomic_cleans_up_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.json"

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(str(target), {"v": 1})
    assert not target.exists()
    leftovers = [name for name in os.listdir(str(tmp_path)) if name.endswith(".tmp")]
    assert leftovers == []
