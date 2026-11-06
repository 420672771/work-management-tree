FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/data
ENV WMT_AUTH_MODE=account WMT_MONTHLY_DEMO=0 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python", "app.py"]
