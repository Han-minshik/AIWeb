import { http } from '../http/client';

export async function searchChatMessages(params = {}) {
  const {
    q,
    session_id,
    role,
    app_type,
    app_id,
    after,
    before,
    order = 'desc',
    limit = 50,
    offset = 0,
  } = params;

  return await http.get('/chat/messages/search', {
    q,
    session_id,
    role,
    app_type,
    app_id,
    after,
    before,
    order,
    limit,
    offset,
  });
}
