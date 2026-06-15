import { z } from 'zod'

const schema = z.object({
  PORT: z.coerce.number().default(36722),
  HOST: z.string().default('0.0.0.0'),
})

export type Config = z.infer<typeof schema>

export function loadConfig(): Config {
  return schema.parse(Bun.env)
}
