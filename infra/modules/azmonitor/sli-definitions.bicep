targetScope = 'subscription'

@description('Azure Service Group name that owns the SLI resources')
param serviceGroupName string

@description('User-assigned managed identity resource ID used by Azure Monitor SLI')
param sliIdentityResourceId string

@description('Managed Prometheus Azure Monitor Workspace resource ID used as source and destination AMW')
param prometheusWorkspaceResourceId string

@description('Azure Monitor SLI metric namespace for Managed Prometheus metrics')
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

@description('Metric name for the request-based Availability SLI good signal')
param availabilityGoodMetricName string = 'chaos_app_external_availability_good'

@description('Metric name for the request-based Availability SLI total signal')
param availabilityTotalMetricName string = 'chaos_app_external_availability_total'

@description('Metric name for the request-based Latency SLI good signal')
param latencyGoodMetricName string = 'chaos_app_external_latency_good'

@description('Metric name for the request-based Latency SLI total signal')
param latencyTotalMetricName string = 'chaos_app_external_latency_total'

@description('Prometheus label dimensions used for Azure Monitor SLI partitioning')
param signalDimensions string[] = [
  'environment'
  'service'
  'test'
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
    description: 'Request-based Availability SLI for external Azure Functions probe results.'
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
    evaluationType: 'RequestBased'
    sliProperties: {
      goodSignals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: availabilityGoodMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Sum'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Sum'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
      }
      totalSignals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: availabilityTotalMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Sum'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Sum'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
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
    description: 'Request-based Latency SLI for external Azure Functions probe duration.'
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
    evaluationType: 'RequestBased'
    sliProperties: {
      goodSignals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: latencyGoodMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Sum'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Sum'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
      }
      totalSignals: {
        signalFormula: 'A'
        signalSources: [
          union(sourceSignalIdentityProperties, {
            filters: []
            metricName: latencyTotalMetricName
            metricNamespace: metricNamespace
            signalSourceId: 'A'
            spatialAggregation: {
              type: 'Sum'
              dimensions: signalDimensions
            }
            temporalAggregation: {
              type: 'Sum'
              windowSizeMinutes: windowSizeMinutes
            }
          })
        ]
      }
    }
  }
}

output availabilitySliId string = availabilitySli.id
output latencySliId string = latencySli.id
