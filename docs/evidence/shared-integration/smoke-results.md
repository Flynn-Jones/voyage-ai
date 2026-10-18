# Shared integration smoke run

- Run: `6652f0` at 2026-10-18T08:03:59+00:00 (UTC), 39 s
- Git: `integrate/shared-ai-release-1 (uncommitted changes)`
- Shared servers: {'mcp-server': 'started pid 37812 (C:/Users/Oat/AppData/Local/Temp/claude/c--Users-Oat-OneDrive---UTS-Spring-2026-Advanced-Software-Development-voyage-ai/1bfaaeae-ebf8-4dd2-b0aa-3f4418e63c49/scratchpad/venv/Scripts/python.exe ai-services/mcp-server/mcp_http_server.py)', 'rag-server': 'started pid 29540 (C:/Users/Oat/AppData/Local/Temp/claude/c--Users-Oat-OneDrive---UTS-Spring-2026-Advanced-Software-Development-voyage-ai/1bfaaeae-ebf8-4dd2-b0aa-3f4418e63c49/scratchpad/venv/Scripts/python.exe ai-services/rag-server/rag_http_server.py)'}
- Ollama models: ['qwen2.5:0.5b', 'qwen2.5:7b', 'gpt-oss:20b']
- RAG refresh: {'status': 'success', 'chunk_count': 149, 'service_counts': {'accommodation': 35, 'activity': 78, 'budget': 13, 'destination': 10, 'itinerary': 12, 'shared': 1}, 'source_status': {'destination_db': 'ok', 'budget_db': 'ok', 'accommodation_db': 'ok', 'activity_db': 'ok', 'itinerary_db': 'ok'}, 'vector_store_status': 'ready'}

## Backend wiring (read from inside each running container)

| Feature | MCP_SERVICE_URL | RAG_SERVICE_URL | MCP_ENABLED | RAG_ENABLED |
| --- | --- | --- | --- | --- |
| destination | http://host.docker.internal:7001 | http://host.docker.internal:7002 | true | true |
| accommodation | http://host.docker.internal:7001 | http://host.docker.internal:7002 | true | true |
| activity | http://host.docker.internal:7001 | http://host.docker.internal:7002 | true | true |
| budget | http://host.docker.internal:7001 | http://host.docker.internal:7002 | true | true |
| itinerary | http://host.docker.internal:7001 | http://host.docker.internal:7002 | true | true |

## Summary

| Feature | DB CRUD | AI Mode | MCP Mode | RAG Mode |
| --- | --- | --- | --- | --- |
| destination | PASS (5/5) | PASS (1/1) | PASS (1/1) | PASS (1/1) |
| accommodation | PASS (5/5) | PASS (1/1) | PASS (1/1) | PASS (1/1) |
| activity | PASS (6/6) | PASS (1/1) | PASS (2/2) | PASS (2/2) |
| budget | PASS (5/5) | PASS (1/1) | PASS (1/1) | PASS (1/1) |
| itinerary | PASS (5/5) | PASS (1/1) | PASS (1/1) | PASS (2/2) |

## Every check

| Feature | Mode | Step | Request | HTTP | Result | Detail |
| --- | --- | --- | --- | --- | --- | --- |
| destination | CRUD | create | `POST /api/destinations` | 201 | PASS | keys=average_daily_cost,categories,city,country,description,destination_id,recommended_trip_length,travel_style |
| destination | CRUD | read | `GET /api/destinations/15` | 200 | PASS | keys=average_daily_cost,categories,city,country,description,destination_id,recommended_trip_length,travel_style |
| destination | CRUD | update | `PUT /api/destinations/15` | 200 | PASS | keys=average_daily_cost,categories,city,country,description,destination_id,recommended_trip_length,travel_style |
| destination | CRUD | delete | `DELETE /api/destinations/15` | 204 | PASS |  |
| destination | CRUD | read-after-delete | `GET /api/destinations/15` | 404 | PASS | keys=error |
| destination | AI | ai-compare | `POST /api/destinations/ai-compare` | 200 | PASS | keys=comparison,destinations,elapsed_seconds,model,preferences |
| destination | MCP | mcp-search (country) | `POST /api/destinations/mcp-search` | 200 | PASS | status='success' |
| destination | RAG | rag-answer | `POST /api/destinations/rag-answer` | 200 | PASS | confidence=High citations=['destination_3', 'destination_5', 'destination_1'] answer='Osaka, Japan is known for street food and nightlife.' |
| accommodation | CRUD | create | `POST /accommodations` | 201 | PASS | keys=amenities,created_at,description,destination_city,destination_id,id,latitude,location |
| accommodation | CRUD | read | `GET /accommodations/22` | 200 | PASS | keys=amenities,created_at,description,destination_city,destination_id,id,latitude,location |
| accommodation | CRUD | update | `PATCH /accommodations/22` | 200 | PASS | keys=amenities,created_at,description,destination_city,destination_id,id,latitude,location |
| accommodation | CRUD | delete | `DELETE /accommodations/22` | 204 | PASS |  |
| accommodation | CRUD | read-after-delete | `GET /accommodations/22` | 404 | PASS | keys=error |
| accommodation | AI | ai/recommend | `POST /ai/recommend` | 200 | PASS | keys=act_result_count,observation,plan,recommendation,recommendations |
| accommodation | MCP | mcp/by-destination | `POST /accommodation/mcp/by-destination` | 200 | PASS | status='success' |
| accommodation | RAG | rag/answer | `POST /accommodation/rag/answer` | 200 | PASS | confidence=Medium citations=['accommodation_2', 'accommodation_4', 'accommodation_3', 'accommodation_1'] answer='In Tokyo, you have several accommodation option |
| activity | CRUD | create | `POST /api/add_activity` | 201 | PASS | keys=activity_cost,activity_id,activity_name,activity_type,duration |
| activity | CRUD | read | `GET /api/view_activity/20` | 200 | PASS | keys=activity_cost,activity_id,activity_name,activity_type,duration |
| activity | CRUD | update | `PATCH /api/edit_activity/20` | 200 | PASS | keys=activity_cost,activity_id,activity_name,activity_type,duration |
| activity | CRUD | delete | `DELETE /api/delete_activity/20` | 200 | PASS | message='deleted' |
| activity | CRUD | read-after-delete | `GET /api/view_activity/20` | 404 | PASS | keys=error |
| activity | CRUD | list | `GET /api/view_activities` | 200 | PASS | list[15] |
| activity | AI | ai-summary | `POST /api/activity/ai-summary` | 200 | PASS | summary='The Harbour Bridge Climb adventure itinerary app offers a 3.5-hour adventure trip to the historic Vi' |
| activity | MCP | mcp/list-activities | `POST /api/activity/mcp/list-activities` | 200 | PASS | tool=list_activities count=15 |
| activity | MCP | mcp/activity | `POST /api/activity/mcp/activity` | 200 | PASS | tool=get_activity keys=activity |
| activity | RAG | rag/answer (count) | `POST /api/activity/rag/answer` | 200 | PASS | confidence=High citations=['activity_count'] answer='There are 15 activities in total.' |
| activity | RAG | rag/retrieve | `POST /api/activity/rag/retrieve` | 200 | PASS | scope=activity ids=['activity_8', 'activity_7', 'activity_category_food'] |
| budget | CRUD | create | `POST /expenses` | 201 | PASS | <dl class="expense-detail" data-expense-id="68"><dt>ID</dt><dd>68</dd><dt>Trip</dt><dd>SMOKE-6652f0< |
| budget | CRUD | read | `GET /expenses/68` | 200 | PASS | <dl class="expense-detail" data-expense-id="68"><dt>ID</dt><dd>68</dd><dt>Trip</dt><dd>SMOKE-6652f0< |
| budget | CRUD | update | `PUT /expenses/68` | 200 | PASS | <dl class="expense-detail" data-expense-id="68"><dt>ID</dt><dd>68</dd><dt>Trip</dt><dd>SMOKE-6652f0< |
| budget | AI | ai-analyse | `POST /budget/ai-analyse` | 200 | PASS | analysis='There is one overspend identified: the cost of smoke ramen was $25.00, which is $5.00 more than the ' |
| budget | MCP | mcp/list-expenses | `POST /budget/mcp/list-expenses` | 200 | PASS | status='success' |
| budget | RAG | rag/answer | `POST /budget/rag/answer` | 200 | PASS | confidence=Insufficient citations=[] answer='Insufficient evidence to answer this question from the current corpus.' |
| budget | CRUD | delete | `DELETE /expenses/68` | 204 | PASS |  |
| budget | CRUD | read-after-delete | `GET /expenses/68` | 404 | PASS | <p class="error">Expense not found.</p> |
| itinerary | CRUD | create | `POST /api/itinerary` | 201 | PASS | keys=activity_id,day,destination_id,end_time,estimated_cost,itinerary_item_id,notes,start_time |
| itinerary | CRUD | read | `GET /api/itinerary/16` | 200 | PASS | keys=activity,activity_id,day,destination,destination_id,end_time,estimated_cost,itinerary_item_id |
| itinerary | CRUD | update | `PUT /api/itinerary/16` | 200 | PASS | keys=activity_id,day,destination_id,end_time,estimated_cost,itinerary_item_id,notes,start_time |
| itinerary | AI | ai-review | `POST /api/itinerary/ai-review` | 200 | PASS | keys=act,adapt,observe,plan |
| itinerary | MCP | mcp/itinerary | `POST /api/itinerary/mcp/itinerary` | 200 | PASS | keys=count,day,items,read_only,source,trip_reference |
| itinerary | RAG | rag/refresh | `POST /api/itinerary/rag/refresh` | 200 | PASS | status='success' |
| itinerary | RAG | rag/answer | `POST /api/itinerary/rag/answer` | 200 | PASS | confidence=High citations=['itinerary_16'] answer='The planned activity for this trip on day 9 is a smoke test from 09:00 to 10:00 ' |
| itinerary | CRUD | delete | `DELETE /api/itinerary/16` | 204 | PASS |  |
| itinerary | CRUD | read-after-delete | `GET /api/itinerary/16` | 404 | PASS | keys=error |
