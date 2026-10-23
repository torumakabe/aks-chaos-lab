targetScope = 'subscription'

@description('Workload/application name')
param appName string = 'aks-chaos-lab'

@description('Environment name')
param environment string

@description('Enable Azure Monitor SLI finalization layer')
param enabled bool = true

@description('Resource group name that receives SLI metric alert resources')
param resourceGroupName string = ''

@description('Azure Service Group resource ID used by Azure Monitor SLI')
param serviceGroupId string = ''

@description('Azure Service Group name used by Azure Monitor SLI')
param serviceGroupName string = ''

@description('User-assigned managed identity resource ID used by Azure Monitor SLI')
param sliIdentityResourceId string = ''

@description('Managed Prometheus Azure Monitor Workspace resource ID')
param prometheusWorkspaceResourceId string = ''

@description('Metric namespace used by Azure Monitor SLI for Managed Prometheus metrics')
param metricNamespace string = 'customdefault'

@description('Enable Azure Monitor SLI baseline / short-window / long-window metric alerts')
param enableSliAlerts bool = true

@description('Availability SLI resource name')
param availabilitySliName string = ''

@description('Latency SLI resource name')
param latencySliName string = ''

@description('External SLI probe name emitted in the Prometheus test label')
param externalSliProbeName string = ''

@description('Availability SLI baseline target percentage')
@minValue(0)
@maxValue(100)
param availabilityBaselineTargetPercent int = 99

@description('Latency SLI baseline target percentage')
@minValue(0)
@maxValue(100)
param latencyBaselineTargetPercent int = 95

@description('Bucket boundary `le` label string used by the request-based Latency SLI good signal. Must match a bucket emitted by the publisher (e.g. "1" for 1 second).')
@allowed([
  '0.1'
  '0.25'
  '0.5'
  '1'
  '2'
  '5'
])
param latencyThresholdLe string = '1'

@description('Azure Monitor SLI evaluation period in days')
@minValue(1)
@maxValue(90)
param evaluationPeriodDays int = 30

@description('Azure Monitor SLI signal aggregation window size in minutes')
@minValue(1)
param windowSizeMinutes int = 5

@description('Prometheus label dimensions used for Azure Monitor SLI partitioning')
param signalDimensions string[] = [
  'environment'
  'service'
  'test'
]

@description('Minimum failed probe windows required by the Azure Monitor SLI short-window alert')
@minValue(1)
param shortWindowFailedProbeThreshold int = 5

@description('Azure Monitor SLI short-window alert lookback in minutes')
@minValue(1)
param shortWindowLookbackMinutes int = 15

@description('Azure Monitor SLI long-window burn-rate alert threshold')
@minValue(1)
param longWindowBurnRateThreshold int = 6

@description('Azure Monitor SLI long-window burn-rate alert lookback in hours')
@minValue(1)
param longWindowLookbackHours int = 6

@description('Action Group resource ID for alerts')
param actionGroupId string = ''

var normalizedResourceGroupName = resourceGroupName == 'none' ? '' : resourceGroupName
var normalizedServiceGroupId = serviceGroupId == 'none' ? '' : serviceGroupId
var normalizedServiceGroupName = serviceGroupName == 'none' ? '' : serviceGroupName
var normalizedSliIdentityResourceId = sliIdentityResourceId == 'none' ? '' : sliIdentityResourceId
var normalizedPrometheusWorkspaceResourceId = prometheusWorkspaceResourceId == 'none'
  ? ''
  : prometheusWorkspaceResourceId
var normalizedAvailabilitySliName = availabilitySliName == 'none' ? '' : availabilitySliName
var normalizedLatencySliName = latencySliName == 'none' ? '' : latencySliName
var normalizedActionGroupId = actionGroupId == 'none' ? '' : actionGroupId
var effectiveResourceGroupName = empty(normalizedResourceGroupName)
  ? 'rg-${appName}-${environment}'
  : normalizedResourceGroupName
var effectiveServiceGroupName = !empty(normalizedServiceGroupName)
  ? normalizedServiceGroupName
  : (!empty(normalizedServiceGroupId) ? last(split(normalizedServiceGroupId, '/')) : '')
var tags = {
  'azd-env-name': environment
}
var hasServiceGroup = !empty(effectiveServiceGroupName) && !empty(normalizedServiceGroupId)
var hasSliPipeline = !empty(normalizedSliIdentityResourceId) && !empty(normalizedPrometheusWorkspaceResourceId)
var hasSliNames = !empty(normalizedAvailabilitySliName) && !empty(normalizedLatencySliName)
var canCreateSli = enabled && hasServiceGroup && hasSliPipeline && hasSliNames

// metric alert rule は AMW と同じリージョンに作成する。別リージョンでは評価されない。
resource prometheusWorkspace 'Microsoft.Monitor/accounts@2025-10-03' existing = if (canCreateSli && enableSliAlerts) {
  scope: resourceGroup(effectiveResourceGroupName)
  name: last(split(normalizedPrometheusWorkspaceResourceId, '/'))
}

module azureMonitorSliDefinitions '../modules/azmonitor/sli-definitions.bicep' = if (canCreateSli) {
  // Subscription-scoped deployment name; suffix with environment so different
  // regions don't collide.
  name: 'azureMonitorSliDefinitions-${environment}'
  params: {
    serviceGroupName: effectiveServiceGroupName
    sliIdentityResourceId: normalizedSliIdentityResourceId
    prometheusWorkspaceResourceId: normalizedPrometheusWorkspaceResourceId
    metricNamespace: metricNamespace
    enableSliAlerts: enableSliAlerts
    availabilitySliName: normalizedAvailabilitySliName
    latencySliName: normalizedLatencySliName
    availabilityBaselineTargetPercent: availabilityBaselineTargetPercent
    latencyBaselineTargetPercent: latencyBaselineTargetPercent
    latencyThresholdLe: latencyThresholdLe
    evaluationPeriodDays: evaluationPeriodDays
    windowSizeMinutes: windowSizeMinutes
    signalDimensions: signalDimensions
  }
}

module azureMonitorSliMetricAlerts '../modules/azmonitor/sli-metric-alerts.bicep' = if (canCreateSli && enableSliAlerts) {
  name: 'azureMonitorSliMetricAlerts'
  scope: resourceGroup(effectiveResourceGroupName)
  params: {
    #disable-next-line BCP318
    location: prometheusWorkspace.location
    tags: tags
    prometheusWorkspaceResourceId: normalizedPrometheusWorkspaceResourceId
    sliIdentityResourceId: normalizedSliIdentityResourceId
    actionGroupId: normalizedActionGroupId
    serviceGroupId: normalizedServiceGroupId
    environment: environment
    externalSliProbeName: externalSliProbeName
    #disable-next-line BCP318
    availabilitySliId: azureMonitorSliDefinitions.outputs.availabilitySliId
    availabilitySliName: normalizedAvailabilitySliName
    #disable-next-line BCP318
    latencySliId: azureMonitorSliDefinitions.outputs.latencySliId
    latencySliName: normalizedLatencySliName
    availabilityBaselineTargetPercent: availabilityBaselineTargetPercent
    latencyBaselineTargetPercent: latencyBaselineTargetPercent
    latencyThresholdLe: latencyThresholdLe
    evaluationPeriodDays: evaluationPeriodDays
    shortWindowFailedProbeThreshold: shortWindowFailedProbeThreshold
    shortWindowLookbackMinutes: shortWindowLookbackMinutes
    longWindowBurnRateThreshold: longWindowBurnRateThreshold
    longWindowLookbackHours: longWindowLookbackHours
  }
}

@description('Azure Monitor Availability SLI resource ID')
#disable-next-line BCP318
output AZURE_MONITOR_AVAILABILITY_SLI_ID string = canCreateSli
  ? azureMonitorSliDefinitions.outputs.availabilitySliId
  : ''

@description('Azure Monitor Latency SLI resource ID')
#disable-next-line BCP318
output AZURE_MONITOR_LATENCY_SLI_ID string = canCreateSli ? azureMonitorSliDefinitions.outputs.latencySliId : ''

@description('Bucket boundary `le` label string used by the Latency SLI good signal filter')
output AZURE_MONITOR_LATENCY_SLI_THRESHOLD_LE string = latencyThresholdLe

@description('Azure Monitor SLI baseline alert resource IDs')
#disable-next-line BCP318
output AZURE_MONITOR_SLI_BASELINE_ALERT_IDS array = canCreateSli && enableSliAlerts
  ? azureMonitorSliMetricAlerts.outputs.baselineAlertIds
  : []

@description('Azure Monitor SLI short-window alert resource IDs')
#disable-next-line BCP318
output AZURE_MONITOR_SLI_SHORT_WINDOW_ALERT_IDS array = canCreateSli && enableSliAlerts
  ? azureMonitorSliMetricAlerts.outputs.shortWindowAlertIds
  : []

@description('Azure Monitor SLI long-window alert resource IDs')
#disable-next-line BCP318
output AZURE_MONITOR_SLI_LONG_WINDOW_ALERT_IDS array = canCreateSli && enableSliAlerts
  ? azureMonitorSliMetricAlerts.outputs.longWindowAlertIds
  : []
