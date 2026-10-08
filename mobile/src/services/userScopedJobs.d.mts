export type UserJobKey = 'hm-food-job' | 'hm-motion-job'
export interface UserJobPointer {
  owner_user_id: number
  [key: string]: unknown
}
export function readUserJob(key: UserJobKey, userId: number | undefined, storage?: Storage): UserJobPointer | null
export function writeUserJob(key: UserJobKey, userId: number | undefined, value: Record<string, unknown>, storage?: Storage): void
export function clearUserJob(key: UserJobKey, storage?: Storage): void
export function clearAllUserJobs(storage?: Storage): void
