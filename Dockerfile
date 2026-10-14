FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN mkdir -p /app/data
EXPOSE 8080
CMD ["python", "app.py"]
