FROM python:3.13-slim
WORKDIR /app
COPY agent-workshop-cloud-capsule.b64 /tmp/source.b64
RUN base64 -d /tmp/source.b64 > /tmp/app.tar.gz \
 && tar -xzf /tmp/app.tar.gz -C /app \
 && pip install --no-cache-dir -r requirements.txt \
 && rm -f /tmp/source.b64 /tmp/app.tar.gz
RUN python -c 'from pathlib import Path; p=Path("web/index.html"); s=p.read_text(); s=s.replace("Database persistence is being configured.", "Persistent storage is connected; onboarding and security review remain pending."); s=s.replace("until persistent storage is enabled.", "until onboarding and security review are complete."); p.write_text(s)'
RUN adduser --system --group appuser && mkdir -p /data && chown appuser:appuser /data
ENV HOST=0.0.0.0 TRUST_PROXY=1
USER appuser
EXPOSE 10000
CMD ["python", "server.py"]
