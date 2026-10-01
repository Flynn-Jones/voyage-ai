$ErrorActionPreference = 'Stop'
$composeArgs = @('-f', 'itinerary-service/docker-compose.yml', '-f', 'itinerary-service/reports/final-runtime/model.override.yml')
try {
    $env:MCP_ENABLED = 'false'
    $env:RAG_ENABLED = 'false'
    docker compose @composeArgs up -d --force-recreate itinerary-be itinerary-fe
    if ($LASTEXITCODE -ne 0) { throw 'Disabled startup failed' }
    @'
import requests,json
checks={}
for route in ['mcp/itinerary','rag/answer','rag/retrieve','rag/refresh']:
 r=requests.post('http://localhost:3005/api/itinerary/'+route,json={},timeout=10)
 assert r.status_code==403,(route,r.text)
 checks[route]=r.status_code
assert requests.get('http://localhost:3005/api/itinerary',timeout=30).status_code==200
open('itinerary-service/reports/final-runtime/disabled-results.json','w').write(json.dumps(checks,indent=2))
print('Disabled routes and Release 0 read: PASS')
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw 'Disabled checks failed' }
    $env:MCP_ENABLED = 'true'
    $env:RAG_ENABLED = 'true'
    $env:MCP_SERVICE_URL = 'http://host.docker.internal:65534'
    $env:RAG_SERVICE_URL = 'http://host.docker.internal:65534'
    docker compose @composeArgs up -d --force-recreate itinerary-be itinerary-fe
    if ($LASTEXITCODE -ne 0) { throw 'Unavailable startup failed' }
    @'
import requests,json
checks={}
for route in ['mcp/itinerary','rag/answer']:
 r=requests.post('http://localhost:3005/api/itinerary/'+route,json={'trip_reference':'TRIP-1001','query':'What is the estimated itinerary cost?'},timeout=40)
 assert r.status_code==502,(route,r.text)
 checks[route]={'http_status':r.status_code,'body':r.json()}
assert requests.get('http://localhost:3005/api/itinerary',timeout=30).status_code==200
open('itinerary-service/reports/final-runtime/unavailable-results.json','w').write(json.dumps(checks,indent=2))
print('Unavailable routes and Release 0 read: PASS')
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw 'Unavailable checks failed' }
}
finally {
    Remove-Item Env:MCP_ENABLED,Env:RAG_ENABLED,Env:MCP_SERVICE_URL,Env:RAG_SERVICE_URL -ErrorAction SilentlyContinue
    docker compose @composeArgs up -d --force-recreate itinerary-be itinerary-fe
    if ($LASTEXITCODE -ne 0) { throw 'Restoring enabled services failed' }
}
