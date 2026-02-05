# Redis Query Commands

This document provides a guide for querying the AstroCat Redis database directly using the command line.

## Connecting to Redis

To access the Redis CLI inside the running Docker container:

```powershell
docker-compose exec redis redis-cli
```

## Basic Inspection Commands

| Command | Description |
| :--- | :--- |
| **`KEYS *`** | List all keys in the current database (usually DB 0). |
| **`DBSIZE`** | Show total number of keys. |
| **`TYPE <key>`** | Check if a key is a List (queue), Hash (status), or String. |
| **`INFO memory`** | View memory usage statistics. |
| **`FLUSHALL`** | **CAUTION:** Deletes everything in all Redis databases. |

## Querying Task Queues (Lists)

AstroCat uses specific lists for task management in DB 0.

### Check Queue Lengths
```bash
LLEN celery     # Main task queue
LLEN indexer    # Directory scanning tasks
LLEN thumbnails # Thumbnail generation tasks
```

### Peek at Tasks
To see the actual content of the last 5 items added to a queue:
```bash
LRANGE indexer 0 4
```
*Note: Task bodies are Base64 encoded JSON messages.*

## Querying Bulk Status (Hashes)

Status messages for bulk operations are stored as Hashes.

1. **Find the key name:**
   ```bash
   KEYS bulk:rescan:*
   ```
2. **Read all data in the hash:**
   ```bash
   HGETALL bulk:rescan:<mount_hash>
   ```

## Switching Databases

By default, you are in **DB 0** (Task Broker). Results and state tracking may use other databases:

```bash
SELECT 1     # Switch to the results database
KEYS *       # See IDs of completed/failed tasks
GET <key>    # Read a specific value
```

## GUI Recommendation

For a more user-friendly interface, you can use **Redis Insight**. Point it to `localhost:6379`. It provides a clean browser for viewing lists, hashes, and decoded Celery headers.
