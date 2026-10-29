import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    title: 'Dashboard',
    loadComponent: () => import('./features/dashboard/dashboard').then((m) => m.Dashboard),
  },
  {
    path: 'machines/:id',
    title: 'Machine',
    loadComponent: () =>
      import('./features/machine-detail/machine-detail').then((m) => m.MachineDetail),
  },
  {
    path: 'graph',
    title: 'Graph explorer',
    loadComponent: () =>
      import('./features/graph-explorer/graph-explorer').then((m) => m.GraphExplorer),
  },
  {
    path: 'simulator',
    title: 'Simulator',
    loadComponent: () =>
      import('./features/simulator-panel/simulator-panel').then((m) => m.SimulatorPanel),
  },
  { path: '**', redirectTo: 'dashboard' },
];
