import { LineChart } from 'echarts/charts';
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { provideEchartsCore } from 'ngx-echarts';

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

/** Provided by the lazily loaded feature that draws charts, so ECharts stays out of the initial bundle. */
export const provideCharts = () => provideEchartsCore({ echarts });
