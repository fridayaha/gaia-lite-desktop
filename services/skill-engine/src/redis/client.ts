/**
 * Redis connection factory.
 *
 * Creates an ioredis client with the `skill-engine:` key prefix
 * for namespace isolation within a shared Redis instance.
 */

import Redis from "ioredis";

/**
 * Create a Redis client.
 *
 * @param redisUrl - Redis connection URL, e.g. "redis://:password@redis:6379/1"
 * @returns ioredis instance with key prefix configured
 */
export function createRedisClient(redisUrl: string): Redis {
  return new Redis(redisUrl, {
    keyPrefix: "skill-engine:",
    maxRetriesPerRequest: 3,
    retryStrategy(times) {
      const delay = Math.min(times * 200, 5000);
      return delay;
    },
    lazyConnect: true,
  });
}
