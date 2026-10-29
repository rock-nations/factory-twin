import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Alarm, GraphView, MachineDetail, MachineSummary, PlantSummary, TelemetrySeries } from '../models/factory';
import { HealthResponse } from '../models/health';
import { InjectRequest, ResetSummary, SimStatus } from '../models/sim';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  getHealth(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>('/api/health');
  }

  getPlants(): Observable<PlantSummary[]> {
    return this.http.get<PlantSummary[]>('/api/plants');
  }

  getMachines(filter: { line_id?: string; status?: string } = {}): Observable<MachineSummary[]> {
    return this.http.get<MachineSummary[]>('/api/machines', { params: new HttpParams({ fromObject: filter }) });
  }

  getMachine(id: string): Observable<MachineDetail> {
    return this.http.get<MachineDetail>(`/api/machines/${id}`);
  }

  getTelemetry(id: string): Observable<TelemetrySeries> {
    return this.http.get<TelemetrySeries>(`/api/machines/${id}/telemetry`);
  }

  getNeighborhood(nodeId: string, depth = 1): Observable<GraphView> {
    return this.http.get<GraphView>('/api/graph/neighborhood', {
      params: { node_id: nodeId, depth },
    });
  }

  getAlarms(status: string | null = 'open'): Observable<Alarm[]> {
    const params: Record<string, string> = status ? { status } : {};
    return this.http.get<Alarm[]>('/api/alarms', { params });
  }

  ackAlarm(id: string): Observable<{ id: string; status: string }> {
    return this.http.post<{ id: string; status: string }>(`/api/alarms/${encodeURIComponent(id)}/ack`, {});
  }

  getSimStatus(): Observable<SimStatus> {
    return this.http.get<SimStatus>('/api/sim/status');
  }

  simStart(): Observable<unknown> {
    return this.http.post('/api/sim/start', {});
  }

  simStop(): Observable<unknown> {
    return this.http.post('/api/sim/stop', {});
  }

  simSpeed(speed: number): Observable<unknown> {
    return this.http.post('/api/sim/speed', { speed });
  }

  simInject(body: InjectRequest): Observable<unknown> {
    return this.http.post('/api/sim/inject', body);
  }

  simReset(): Observable<ResetSummary> {
    return this.http.post<ResetSummary>('/api/sim/reset', {});
  }
}
