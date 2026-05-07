import apiService, { EvacuationCenter } from '@/services/api';

function defaultSummary(c: EvacuationCenter): string {
  const cap = c.capacity != null ? `About ${c.capacity} people can be accommodated here. ` : '';
  const kind = c.type ? `This is a ${String(c.type).replace(/_/g, ' ')} site. ` : '';
  return (
    `${c.name} is a listed evacuation point in Metro Manila. ${kind}${cap}` +
    'In an emergency, please follow barangay and city officials, bring medicines and IDs, and avoid crossing deep floodwater.'
  );
}

function normalizeCenter(center: EvacuationCenter): EvacuationCenter {
  return {
    ...center,
    safety_score:
      typeof center.safety_score === 'number' && Number.isFinite(center.safety_score)
        ? center.safety_score
        : undefined,
    elevation:
      typeof center.elevation === 'number' && Number.isFinite(center.elevation) ? center.elevation : undefined,
    is_open: center.is_open !== false,
    summary: center.summary ?? defaultSummary(center),
  };
}

export async function resolveNearestOpenCenter(
  user: { latitude: number; longitude: number },
  _centers?: EvacuationCenter[]
): Promise<EvacuationCenter | null> {
  const center = await apiService.getNearestEvacuationCenter(user.latitude, user.longitude, true);
  return center ? normalizeCenter(center) : null;
}

export async function loadEvacuationCenters(): Promise<EvacuationCenter[]> {
  const centers = await apiService.getEvacuationCenters();
  return centers.map(normalizeCenter);
}
