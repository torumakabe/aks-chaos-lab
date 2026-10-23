// rule を AMW と別リージョンに作成すると PromQL が評価されず、alert が発火しない。
// 既定値を置かず、呼び出し側が AMW の location を渡す。
@description('Location of the metric alert rules. Must match the Azure Monitor workspace location')
param location string

@description('Tags applied to all resources')
param tags object = {}

@description('Resource ID of the Azure Monitor managed Prometheus workspace')
param prometheusWorkspaceResourceId string

@description('User-assigned managed identity resource ID used by Azure Monitor SLI and its metric alerts')
param sliIdentityResourceId string

@description('Resource ID of the Action Group for SLI alerts')
param actionGroupId string = ''

@description('Azure Service Group resource ID')
param serviceGroupId string

@description('Environment label emitted by the external SLI publisher')
param environment string

@description('Probe name emitted in the external SLI publisher test label')
param externalSliProbeName string

@description('Availability SLI resource ID')
param availabilitySliId string

@description('Availability SLI resource name')
param availabilitySliName string

@description('Latency SLI resource ID')
param latencySliId string

@description('Latency SLI resource name')
param latencySliName string

@description('Availability SLI target percentage')
@minValue(0)
@maxValue(100)
param availabilityBaselineTargetPercent int

@description('Latency SLI target percentage')
@minValue(0)
@maxValue(100)
param latencyBaselineTargetPercent int

@description('Latency SLI good-signal bucket boundary')
param latencyThresholdLe string

@description('SLI rolling evaluation period in days')
@minValue(1)
param evaluationPeriodDays int

@description('Severity for Azure Monitor SLI metric alerts')
@minValue(0)
@maxValue(4)
param severity int = 3

@description('Minimum failed probe windows in the short-window alert lookback')
@minValue(1)
param shortWindowFailedProbeThreshold int = 5

@description('Short-window alert lookback in minutes')
@minValue(1)
param shortWindowLookbackMinutes int = 15

@description('Long-window burn-rate alert threshold')
@minValue(1)
param longWindowBurnRateThreshold int = 6

@description('Long-window burn-rate alert lookback in hours')
@minValue(1)
param longWindowLookbackHours int = 6

var sliDefinitions = [
  {
    name: availabilitySliName
    id: availabilitySliId
    goodMetricName: 'chaos_app_external_availability_good'
    totalMetricName: 'chaos_app_external_availability_total'
    goodMetricFilter: ''
    targetPercent: availabilityBaselineTargetPercent
  }
  {
    name: latencySliName
    id: latencySliId
    goodMetricName: 'chaos_app_external_latency_good'
    totalMetricName: 'chaos_app_external_latency_total'
    goodMetricFilter: ',le="${latencyThresholdLe}"'
    targetPercent: latencyBaselineTargetPercent
  }
]

var metricLabels = 'environment="${environment}",service="chaos-app",test="${externalSliProbeName}"'
var failingPeriods = {
  for: 'PT1M'
}

var actions = actionGroupId != ''
  ? [
      {
        actionGroupId: actionGroupId
      }
    ]
  : []

resource baselineAlerts 'Microsoft.Insights/metricAlerts@2026-01-01' = [
  for sli in sliDefinitions: {
    name: 'SLI baseline alert for ${sli.name}'
    location: location
    tags: tags
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: {
        '${sliIdentityResourceId}': {}
      }
    }
    properties: {
      description: 'SLI baseline alert for ${sli.name}'
      enabled: true
      severity: severity
      scopes: [
        prometheusWorkspaceResourceId
      ]
      evaluationFrequency: 'PT1M'
      targetResourceType: 'microsoft.monitor/accounts'
      criteria: any({
        'odata.type': 'Microsoft.Azure.Monitor.PromQLCriteria'
        allOf: [
          {
            criterionType: 'StaticThresholdCriterion'
            name: 'SliBaselineCriterion'
            query: 'sum(sum_over_time(${sli.totalMetricName}{${metricLabels}}[${evaluationPeriodDays}d])) >= ${evaluationPeriodDays * 24 * 60} and sum(sum_over_time(${sli.goodMetricName}{${metricLabels}${sli.goodMetricFilter}}[${evaluationPeriodDays}d])) / sum(sum_over_time(${sli.totalMetricName}{${metricLabels}}[${evaluationPeriodDays}d])) * 100 < ${sli.targetPercent}'
          }
        ]
        failingPeriods: failingPeriods
      })
      actions: actions
      #disable-next-line BCP037
      customProperties: {
        serviceGroupId: serviceGroupId
        sliId: sli.id
        targetPercent: string(sli.targetPercent)
        evaluationPeriodDays: string(evaluationPeriodDays)
      }
      #disable-next-line BCP037
      resolveConfiguration: {
        autoResolved: true
        timeToResolve: 'PT2M'
      }
    }
  }
]

resource shortWindowAlerts 'Microsoft.Insights/metricAlerts@2026-01-01' = [
  for sli in sliDefinitions: {
    name: 'SLI short-window alert for ${sli.name}'
    location: location
    tags: tags
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: {
        '${sliIdentityResourceId}': {}
      }
    }
    properties: {
      description: 'SLI short-window alert for ${sli.name}. At least ${shortWindowFailedProbeThreshold} failed probe windows in the last ${shortWindowLookbackMinutes} minutes.'
      enabled: true
      severity: severity
      scopes: [
        prometheusWorkspaceResourceId
      ]
      evaluationFrequency: 'PT1M'
      targetResourceType: 'microsoft.monitor/accounts'
      criteria: any({
        'odata.type': 'Microsoft.Azure.Monitor.PromQLCriteria'
        allOf: [
          {
            criterionType: 'StaticThresholdCriterion'
            name: 'SliShortWindowFailureCriterion'
            query: 'sum(sum_over_time(${sli.totalMetricName}{${metricLabels}}[${shortWindowLookbackMinutes}m])) - sum(sum_over_time(${sli.goodMetricName}{${metricLabels}${sli.goodMetricFilter}}[${shortWindowLookbackMinutes}m])) >= ${shortWindowFailedProbeThreshold}'
          }
        ]
        failingPeriods: failingPeriods
      })
      actions: actions
      #disable-next-line BCP037
      customProperties: {
        failedProbeThreshold: string(shortWindowFailedProbeThreshold)
        lookbackMinutes: string(shortWindowLookbackMinutes)
        serviceGroupId: serviceGroupId
        sliId: sli.id
      }
      #disable-next-line BCP037
      resolveConfiguration: {
        autoResolved: true
        timeToResolve: 'PT2M'
      }
    }
  }
]

resource longWindowAlerts 'Microsoft.Insights/metricAlerts@2026-01-01' = [
  for sli in sliDefinitions: {
    name: 'SLI long-window alert for ${sli.name}'
    location: location
    tags: tags
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: {
        '${sliIdentityResourceId}': {}
      }
    }
    properties: {
      description: 'SLI long-window alert for ${sli.name}. Burn rate threshold: ${longWindowBurnRateThreshold} over the last ${longWindowLookbackHours} hour(s).'
      enabled: true
      severity: severity
      scopes: [
        prometheusWorkspaceResourceId
      ]
      evaluationFrequency: 'PT1M'
      targetResourceType: 'microsoft.monitor/accounts'
      criteria: any({
        'odata.type': 'Microsoft.Azure.Monitor.PromQLCriteria'
        allOf: [
          {
            criterionType: 'StaticThresholdCriterion'
            name: 'SliLongWindowBurnRateCriterion'
            query: sli.targetPercent == 100
              ? '0 >= 1'
              : '(sum(sum_over_time(${sli.totalMetricName}{${metricLabels}}[${longWindowLookbackHours}h])) - sum(sum_over_time(${sli.goodMetricName}{${metricLabels}${sli.goodMetricFilter}}[${longWindowLookbackHours}h]))) / sum(sum_over_time(${sli.totalMetricName}{${metricLabels}}[${longWindowLookbackHours}h])) * 100 / ${100 - sli.targetPercent} >= ${longWindowBurnRateThreshold}'
          }
        ]
        failingPeriods: failingPeriods
      })
      actions: actions
      #disable-next-line BCP037
      customProperties: {
        burnRate: string(longWindowBurnRateThreshold)
        lookbackHours: string(longWindowLookbackHours)
        serviceGroupId: serviceGroupId
        sliId: sli.id
      }
      #disable-next-line BCP037
      resolveConfiguration: {
        autoResolved: true
        timeToResolve: 'PT2M'
      }
    }
  }
]

output baselineAlertIds array = [for (sli, i) in sliDefinitions: baselineAlerts[i].id]
output shortWindowAlertIds array = [for (sli, i) in sliDefinitions: shortWindowAlerts[i].id]
output longWindowAlertIds array = [for (sli, i) in sliDefinitions: longWindowAlerts[i].id]
