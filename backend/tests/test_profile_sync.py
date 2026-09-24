# -*- coding: utf-8 -*-
"""Tests for nickname / avatar profile sync."""


def test_me_update_nickname_and_avatar(api):
    res = api.put(
        "/api/v1/users/me",
        json={"nickname": "小张", "avatar_url": "https://cdn.example.com/a.jpg"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["nickname"] == "小张"
    assert body["avatar_url"] == "https://cdn.example.com/a.jpg"

    me = api.get("/api/v1/users/me")
    assert me.json()["nickname"] == "小张"
    assert me.json()["avatar_url"] == "https://cdn.example.com/a.jpg"


def test_me_accepts_cloud_file_id(api):
    res = api.put(
        "/api/v1/users/me",
        json={"nickname": "  ", "avatar_url": "cloud://env/x/avatar.png"},
    )
    # blank nickname rejected
    assert res.status_code == 422
    res = api.put("/api/v1/users/me", json={"avatar_url": "cloud://env/x/avatar.png"})
    assert res.status_code == 200
    assert res.json()["avatar_url"].startswith("cloud://")


def test_me_rejects_unsupported_avatar(api):
    res = api.put(
        "/api/v1/users/me", json={"avatar_url": "javascript:alert(1)"}
    )
    assert res.status_code == 422
