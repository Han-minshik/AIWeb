from unittest.mock import patch

from app.services.features import chat_service


def test_delete_session_uses_postgresql_delete_syntax():
    with patch.object(chat_service, "fetch_one", return_value={"session_id": 42}), patch.object(chat_service, "execute") as execute:
        result = chat_service.delete_session(42, user_id=7)

    sql = " ".join(execute.call_args_list[0].args[0].split())
    assert sql == "DELETE FROM chat_feedback f USING chat_messages m WHERE m.message_id = f.msg_id AND m.session_id = %s"
    assert [item.args[1] for item in execute.call_args_list] == [(42,), (42,), (42,)]
    assert execute.call_count == 3
    assert result == {"message": "Session deleted successfully."}
