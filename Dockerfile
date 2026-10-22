FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLANE_MCP_HTTP=1 \
    PLANE_MCP_DATA=/tmp/plane-mcp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plane_mcp.py .

CMD ["python", "plane_mcp.py"]
