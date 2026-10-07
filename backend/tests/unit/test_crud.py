from app.db import crud


def test_create_and_get_user(db_session):
    user = crud.create_user(db_session, username="alice", email="alice@example.com", hashed_password="hashed123")
    assert user.id is not None
    assert crud.get_user_by_username(db_session, "alice").id == user.id
    assert crud.get_user_by_email(db_session, "alice@example.com").id == user.id


def test_get_user_by_username_missing_returns_none(db_session):
    assert crud.get_user_by_username(db_session, "nobody") is None


def test_create_conversation_and_list_for_user(db_session):
    user = crud.create_user(db_session, username="bob", email="bob@example.com", hashed_password="x")
    conv1 = crud.create_conversation(db_session, user_id=user.id, title="First chat")
    conv2 = crud.create_conversation(db_session, user_id=user.id, title="Second chat")

    conversations = crud.list_conversations_for_user(db_session, user_id=user.id)
    assert {c.id for c in conversations} == {conv1.id, conv2.id}


def test_add_message_and_list_messages(db_session):
    user = crud.create_user(db_session, username="carol", email="carol@example.com", hashed_password="x")
    conversation = crud.create_conversation(db_session, user_id=user.id)

    crud.add_message(db_session, conversation.id, role="user", content="Hello")
    crud.add_message(db_session, conversation.id, role="assistant", content="Hi there")

    messages = crud.list_messages(db_session, conversation.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert [m.content for m in messages] == ["Hello", "Hi there"]


def test_delete_conversation_cascades_messages(db_session):
    user = crud.create_user(db_session, username="dave", email="dave@example.com", hashed_password="x")
    conversation = crud.create_conversation(db_session, user_id=user.id)
    crud.add_message(db_session, conversation.id, role="user", content="Hello")

    crud.delete_conversation(db_session, conversation)

    assert crud.get_conversation(db_session, conversation.id) is None
    assert crud.list_messages(db_session, conversation.id) == []