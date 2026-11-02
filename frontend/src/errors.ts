import { ApiError } from "./api";

export const errText = (e: unknown) => (e instanceof ApiError ? `${e.message}（${e.code}${e.status ? ` · HTTP ${e.status}` : ""}）` : String(e));
