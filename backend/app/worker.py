from redis import Redis
from rq import Worker

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    Worker(["ingestion"], connection=connection).work()


if __name__ == "__main__":
    main()
