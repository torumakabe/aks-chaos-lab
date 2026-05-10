targetScope = 'subscription'

@description('Azure Service Group name that owns the SLI resources')
param serviceGroupName string

@description('User-assigned managed identity resource ID used by Azure Monitor SLI')
param sliIdentityResourceId string

@description('Managed Prometheus Azure Monitor Workspace resource ID used as source and destination AMW')
param prometheusWorkspaceResourceId string

@description('Azure Monitor SLI metric namespace for Managed Prometheus recording rules')
param metricNamespace string = 'customdefault'

@description('Enable Azure Monitor SLI-generated alerts')
param enableSliAlerts bool = false

@description('Availability SLI resource name')
param availabilitySliName string

@description('Latency SLI resource name')
param latencySliName string

@description('Availability SLI baseline target percentage')
@minValue(0)
@maxValue(100)
param availabilityBaselineTargetPercent int = 99

@description('Latency SLI baseline target percentage')
@minValue(0)
@maxValue(100)
param latencyBaselineTargetPercent int = 95

@description('SLI evaluation period in days')
@minValue(1)
@maxValue(90)
param evaluationPeriodDays int = 30

@description('Signal aggregation window size in minutes')
@minValue(1)
param windowSizeMinutes int = 5

@description('Minimum ratio of requests completed within the latency threshold for a good latency window')
param latencyGoodRateTarget string = '0.95'

@description('Metric name for the window-based Availability SLI success-rate signal')
param availabilityMetricName string = 'gateway:chaos_app:http_success_rate:ratio'

@description('Metric name for the window-based Latency SLI threshold satisfaction ratio')
param latencyMetricName string = 'gateway:chaos_app:http_request_duration:le_1s_ratio'

@description('Prometheus label dimensions used for Azure Monitor SLI partitioning')
param signalDimensions string[] = [
  'cluster_name'
]

var sliUserAssignedIdentities = {
  '${sliIdentityResourceId}': {}
}
var destinationAmwAccounts = [
  {
    identity: sliIdentityResourceId
    resourceId: prometheusWorkspaceResourceId
  }
]
var sliIdentity = {
  type: 'UserAssigned'
  userAssignedIdentities: sliUserAssignedIdentities
}
var sourceSignalIdentityProperties = {
  sourceAmwAccountManagedIdentity: sliIdentityResourceId
  sourceAmwAccountResourceId: prometheusWorkspaceResourceId
}

resource serviceGroup 'Microsoft.Management/serviceGroups@2024-02-01-preview' existing = {
  scope: tenant()
  name: serviceGroupName
}

#disable-next-line BCP081
resource availabilitySli 'Microsoft.Monitor/slis@2025-03-01-preview' = {
  name: availabilitySliName
  scope: serviceGroup
  identity: sliIdentity
  properties: {
    description: 'Window-based Availability SLI for chaos app Gateway Envoy success rate.'
    baselineProperties: {
      baseline: {
        evaluationCalculationType: 'RollingDays'
        evaluationPeriodDays: evaluationPeriodDays
        value: availabilityBaselineTargetPercent
      }
    }
    category: 'Availability'
    destinationAmwAccounts: destinationAmwAccounts
    enableAlert: enableSliAlerts
    evaluationType: 'WindowBased'
    sliProperties: {
      signals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: availabilityMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Average'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Average'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
      }
      windowUptimeCriteria: {
        comparator: 'gt'
        target: json('0.99')
      }
    }
  }
}

#disable-next-line BCP081
resource latencySli 'Microsoft.Monitor/slis@2025-03-01-preview' = {
  name: latencySliName
  scope: serviceGroup
  identity: sliIdentity
  properties: {
    description: 'Window-based Latency SLI for chaos app Gateway Envoy requests completed within 1 second.'
    baselineProperties: {
      baseline: {
        evaluationCalculationType: 'RollingDays'
        evaluationPeriodDays: evaluationPeriodDays
        value: latencyBaselineTargetPercent
      }
    }
    category: 'Latency'
    destinationAmwAccounts: destinationAmwAccounts
    enableAlert: enableSliAlerts
    evaluationType: 'WindowBased'
    sliProperties: {
      signals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: latencyMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Average'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Average'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
      }
      windowUptimeCriteria: {
        comparator: 'gte'
        target: json(latencyGoodRateTarget)
      }
    }
  }
}

output availabilitySliId string = availabilitySli.id
output latencySliId string = latencySli.id
