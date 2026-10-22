"""A device can restore its conversations: exchanges are stored and GET /conversations/{id} returns them."""

pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_exchange_creates_the_conversation_and_stores_both_messages(device_token_store):
    db = device_token_store
    db.record_moonpie_exchange("dev", "conv-1", "What is on today?", "You have two meetings.")
    [row] = db.list_moonpie_conversations("dev")
    assert row["id"] == "conv-1"
    assert row["title"] == "What is on today?"
    assert row["message_count"] == 2
    assert [(m["role"], m["content"]) for m in db.list_moonpie_messages("conv-1", "dev")] == [
        ("user", "What is on today?"),
        ("assistant", "You have two meetings."),
    ]


def test_second_exchange_appends_and_keeps_the_title(device_token_store):
    db = device_token_store
    db.record_moonpie_exchange("dev", "conv-1", "First question", "First answer")
    db.record_moonpie_exchange("dev", "conv-1", "Second question", "Second answer")
    [row] = db.list_moonpie_conversations("dev")
    assert row["title"] == "First question"
    assert row["message_count"] == 4
    assert [m["content"] for m in db.list_moonpie_messages("conv-1", "dev")][-2:] == ["Second question", "Second answer"]


def test_empty_text_is_not_stored_and_a_long_title_is_cut(device_token_store):
    db = device_token_store
    db.record_moonpie_exchange("dev", "conv-1", "x " * 100, "")
    [row] = db.list_moonpie_conversations("dev")
    assert len(row["title"]) <= 60
    assert row["message_count"] == 1
    db.record_moonpie_exchange("dev", "conv-2", "", "")
    assert [r["id"] for r in db.list_moonpie_conversations("dev")] == ["conv-1"]


def test_another_device_cannot_write_into_or_read_a_conversation(device_token_store):
    db = device_token_store
    db.record_moonpie_exchange("dev", "conv-1", "mine", "mine too")
    db.record_moonpie_exchange("other", "conv-1", "intruder", "intruder")
    assert [m["content"] for m in db.list_moonpie_messages("conv-1", "dev")] == ["mine", "mine too"]
    assert db.list_moonpie_messages("conv-1", "other") == []


def test_get_conversation_returns_messages_in_order(client, valid_token, device_token_store):
    device_token_store.record_moonpie_exchange("moonpie-test-device", "conv-9", "Hello", "Hi there")
    response = client.get("/api/moonpie/conversations/conv-9", headers=_auth(valid_token))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "conv-9"
    assert body["title"] == "Hello"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [("user", "Hello"), ("assistant", "Hi there")]
    assert body["created_at"].endswith("Z")


def test_get_conversation_of_another_device_is_not_found(client, valid_token, device_token_store):
    device_token_store.record_moonpie_exchange("someone-else", "conv-7", "private", "private")
    response = client.get("/api/moonpie/conversations/conv-7", headers=_auth(valid_token))
    assert response.status_code == 404


def test_list_shows_the_conversation_after_an_exchange(client, valid_token, device_token_store):
    device_token_store.record_moonpie_exchange("moonpie-test-device", "conv-5", "Plan the week", "Here is a plan")
    response = client.get("/api/moonpie/conversations", headers=_auth(valid_token))
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["conv-5"]
