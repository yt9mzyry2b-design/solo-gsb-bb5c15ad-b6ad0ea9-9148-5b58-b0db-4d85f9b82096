from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def invert(document, operations):
    return client.post(
        "/patch/invert", json={"document": document, "operations": operations}
    )


def apply(document, operations):
    return client.post(
        "/patch", json={"document": document, "operations": operations}
    )


def roundtrip(document, operations):
    """Run /patch/invert, then undo the returned document via /patch."""
    r = invert(document, operations)
    assert r.status_code == 200
    body = r.json()
    undo = apply(body["document"], body["inverse_operations"])
    assert undo.status_code == 200
    return body, undo.json()["document"]


# ------------------------------------------------------------ happy path


def test_success_returns_document_and_inverse_operations():
    r = invert({"a": 1}, [{"op": "add", "path": "/b", "value": 2}])
    assert r.status_code == 200
    body = r.json()
    assert body["document"] == {"a": 1, "b": 2}
    assert body["inverse_operations"] == [{"op": "remove", "path": "/b"}]


def test_add_over_existing_object_key_inverts_to_replace_with_old_value():
    # RFC 6902: add on an existing member overwrites it, so the inverse must
    # restore the overwritten value rather than remove the key.
    r = invert({"a": 1}, [{"op": "add", "path": "/a", "value": 2}])
    assert r.status_code == 200
    assert r.json()["document"] == {"a": 2}
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a", "value": 1}
    ]


def test_remove_inverts_to_add_with_old_value():
    r = invert({"a": {"x": [1, 2]}}, [{"op": "remove", "path": "/a"}])
    assert r.status_code == 200
    body = r.json()
    assert body["document"] == {}
    assert body["inverse_operations"] == [
        {"op": "add", "path": "/a", "value": {"x": [1, 2]}}
    ]


def test_replace_inverts_to_replace_with_old_value():
    r = invert({"a": 1}, [{"op": "replace", "path": "/a", "value": 2}])
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a", "value": 1}
    ]


def test_array_insert_inverts_to_remove_at_numeric_index():
    r = invert([1, 2, 3], [{"op": "add", "path": "/1", "value": 9}])
    assert r.json()["document"] == [1, 9, 2, 3]
    assert r.json()["inverse_operations"] == [{"op": "remove", "path": "/1"}]


def test_array_append_dash_inverts_to_remove_at_resolved_index():
    # '-' is not a valid 'remove' target, so the inverse names the index.
    r = invert([1, 2], [{"op": "add", "path": "/-", "value": 3}])
    assert r.json()["document"] == [1, 2, 3]
    assert r.json()["inverse_operations"] == [{"op": "remove", "path": "/2"}]


def test_nested_array_append_resolves_full_path():
    r = invert({"a": {"b": [1]}}, [{"op": "add", "path": "/a/b/-", "value": 2}])
    assert r.json()["inverse_operations"] == [
        {"op": "remove", "path": "/a/b/1"}
    ]


def test_array_remove_inverts_to_add_at_same_index():
    r = invert([1, 2, 3], [{"op": "remove", "path": "/1"}])
    assert r.json()["document"] == [1, 3]
    assert r.json()["inverse_operations"] == [
        {"op": "add", "path": "/1", "value": 2}
    ]


def test_test_operations_contribute_no_inverse():
    operations = [
        {"op": "test", "path": "/a", "value": 1},
        {"op": "replace", "path": "/a", "value": 2},
        {"op": "test", "path": "/a", "value": 2},
    ]
    r = invert({"a": 1}, operations)
    assert r.status_code == 200
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a", "value": 1}
    ]


def test_root_replace_inverts_to_root_replace():
    r = invert({"a": 1}, [{"op": "replace", "path": "", "value": [1]}])
    assert r.json()["document"] == [1]
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "", "value": {"a": 1}}
    ]


def test_root_add_inverts_to_root_replace():
    r = invert(1, [{"op": "add", "path": "", "value": "x"}])
    assert r.json()["document"] == "x"
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "", "value": 1}
    ]


def test_empty_operations_yield_empty_inverse_list():
    r = invert({"a": 1}, [])
    assert r.status_code == 200
    assert r.json() == {"document": {"a": 1}, "inverse_operations": []}


def test_pointer_escaping_is_preserved_in_inverse_paths():
    r = invert({"a/b": 1}, [{"op": "replace", "path": "/a~1b", "value": 2}])
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a~1b", "value": 1}
    ]


# ------------------------------------------------- ordering & index shifts


def test_inverses_are_returned_in_undo_order():
    operations = [
        {"op": "add", "path": "/b", "value": 2},
        {"op": "replace", "path": "/a", "value": 10},
    ]
    r = invert({"a": 1}, operations)
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a", "value": 1},
        {"op": "remove", "path": "/b"},
    ]


def test_inverse_values_reflect_each_step_not_the_original():
    operations = [
        {"op": "replace", "path": "/a", "value": 2},
        {"op": "replace", "path": "/a", "value": 3},
    ]
    r = invert({"a": 1}, operations)
    assert r.json()["inverse_operations"] == [
        {"op": "replace", "path": "/a", "value": 2},
        {"op": "replace", "path": "/a", "value": 1},
    ]


def test_array_index_shifts_roundtrip():
    operations = [
        {"op": "add", "path": "/0", "value": "a"},
        {"op": "add", "path": "/0", "value": "b"},
        {"op": "remove", "path": "/2"},
    ]
    body, restored = roundtrip([1, 2, 3], operations)
    assert body["document"] == ["b", "a", 2, 3]
    assert restored == [1, 2, 3]


def test_roundtrip_through_patch_restores_original():
    body, restored = roundtrip(
        {"a": [1, 2], "b": 3},
        [
            {"op": "test", "path": "/b", "value": 3},
            {"op": "add", "path": "/a/-", "value": 3},
            {"op": "replace", "path": "/b", "value": 30},
            {"op": "add", "path": "/c", "value": "new"},
        ],
    )
    assert body["document"] == {"a": [1, 2, 3], "b": 30, "c": "new"}
    assert restored == {"a": [1, 2], "b": 3}


def test_complex_roundtrip_restores_original_exactly():
    document = {
        "users": [{"name": "ada", "tags": ["a", "b"]}, {"name": "bob"}],
        "count": 2,
        "nested": {"x": {"y": 1}},
    }
    operations = [
        {"op": "add", "path": "/users/0/tags/-", "value": "c"},
        {"op": "remove", "path": "/users/1/name"},
        {"op": "replace", "path": "/count", "value": 3},
        {"op": "add", "path": "/nested/x", "value": {"z": 9}},
        {"op": "test", "path": "/users/0/name", "value": "ada"},
        {"op": "remove", "path": "/users/0/tags/0"},
    ]
    body, restored = roundtrip(document, operations)
    assert restored == document


def test_inverse_operations_can_be_inverted_again():
    # The inverse list is itself a valid operations list for /patch/invert.
    r = invert({"a": 1}, [{"op": "add", "path": "/b", "value": 2}])
    body = r.json()
    r2 = invert(body["document"], body["inverse_operations"])
    assert r2.status_code == 200
    assert r2.json()["document"] == {"a": 1}


# ------------------------------------------------------- failure behaviour


def test_conflict_returns_409_without_partial_results():
    operations = [
        {"op": "add", "path": "/b", "value": 2},
        {"op": "remove", "path": "/missing"},
    ]
    r = invert({"a": 1}, operations)
    assert r.status_code == 409
    body = r.json()
    assert set(body) == {"detail", "failed_index", "path", "reason"}
    assert body["detail"] == "patch conflict"
    assert body["failed_index"] == 1
    assert body["path"] == "/missing"
    assert "document" not in body
    assert "inverse_operations" not in body


def test_failed_test_returns_409():
    r = invert({"a": 1}, [{"op": "test", "path": "/a", "value": 2}])
    assert r.status_code == 409
    assert r.json()["failed_index"] == 0
    assert "inverse_operations" not in r.json()


def test_structure_error_returns_422():
    r = invert({"a": 1}, [{"op": "remove", "path": "/a", "value": 1}])
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "invalid patch operation"
    assert body["failed_index"] == 0
    assert body["path"] == "/a"
    assert "inverse_operations" not in body


def test_unknown_op_is_422():
    r = invert({}, [{"op": "move", "path": "/a", "from": "/b"}])
    assert r.status_code == 422
    assert r.json()["failed_index"] == 0


def test_bad_pointer_is_422():
    r = invert({}, [{"op": "add", "path": "a", "value": 1}])
    assert r.status_code == 422


def test_missing_request_fields_rejected():
    r = client.post("/patch/invert", json={"document": {}})
    assert r.status_code == 422


def test_failed_invert_does_not_mutate_input_document():
    doc = {"a": [1, 2]}
    operations = [
        {"op": "add", "path": "/a/-", "value": 3},
        {"op": "remove", "path": "/x"},
    ]
    r = invert(doc, operations)
    assert r.status_code == 409
    assert doc == {"a": [1, 2]}


def test_success_does_not_mutate_input_document():
    doc = {"a": [1, 2]}
    r = invert(doc, [{"op": "add", "path": "/a/-", "value": 3}])
    assert r.status_code == 200
    assert doc == {"a": [1, 2]}


# -------------------------------------------------- other endpoints intact


def test_patch_still_works():
    r = apply({"a": 1}, [{"op": "replace", "path": "/a", "value": 2}])
    assert r.status_code == 200
    assert r.json() == {"document": {"a": 2}}


def test_health_unchanged():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
