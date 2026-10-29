export interface ComponentHealth {
  status: 'ok' | 'error';
  detail: string | null;
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  neo4j: ComponentHealth;
  mqtt: ComponentHealth;
}
