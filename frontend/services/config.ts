const getBackendBaseUrl = (): string => {
  const raw = process.env.EXPO_PUBLIC_API_URL?.trim();
  if (raw) {
    return raw.replace(/\/$/, '');
  }
  return 'http://127.0.0.1:5000';
};

export const BACKEND_BASE_URL = getBackendBaseUrl();
