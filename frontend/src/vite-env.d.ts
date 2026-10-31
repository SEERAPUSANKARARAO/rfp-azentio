/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Address of the backend in production, such as https://rfp-ai-api.onrender.com. Empty in development. */
  readonly VITE_API_URL?: string;
}
