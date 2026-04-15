Create the following Docker files for the financial-research-agent project:

1. Dockerfile in the project root:
   - Use python:3.11-slim as base image
   - Set working directory to /app
   - Copy requirements.txt first and pip install (this layer gets cached)
   - Then copy the rest of the project code
   - Do NOT set a CMD because docker-compose will override it per service

2. docker-compose.yml in the project root with two services:

   Service 1 called mcp-server:
   - Builds from the Dockerfile
   - Command: python -m mcp_server.server
   - Port mapping: 8000:8000
   - Volume: shared named volume called chroma_data mounted at /app/data
   - Env vars from .env file
   - Healthcheck: curl the MCP server URL every 10s

   Service 2 called api-server:
   - Builds from the same Dockerfile
   - Command: uvicorn api.main:app --host 0.0.0.0 --port 8080
   - Port mapping: 8080:8080
   - Volume: same chroma_data volume mounted at /app/data
   - Env vars from .env file plus MCP_SERVER_URL set to http://mcp-server:8000
   - Depends on mcp-server

   Define the chroma_data volume at the bottom.

3. Create a .dockerignore file that excludes: .env, .git, __pycache__, data/, .venv, *.pyc