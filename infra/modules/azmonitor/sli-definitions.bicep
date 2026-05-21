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

@description('Metric name for the request-based Latency SLI good signal. The publisher emits one sample of this metric per `le` bucket per window with the bucket label as the `le` dimension; the SLI selects a bucket via a `dimensionName=le, operator=EQ, values=[latencyThresholdLe]` filter.')
param latencyGoodMetricName string = 'chaos_app_external_latency_good'

@description('Metric name for the request-based Latency SLI total signal (probe observation count).')
param latencyTotalMetricName string = 'chaos_app_external_latency_total'

@description('Threshold for the Latency SLI good signal, expressed as the upper-bound `le` of a bucket (seconds). Must exactly match a bucket label emitted by the publisher. Changing this value re-points the SLI to a different `le` bucket via a dimension filter, without redeploying the publisher (ADR-013).')
@allowed([
  '0.1'
  '0.25'
  '0.5'
  '1'
  '2'
  '5'
])
param latencyThresholdLe string = '1'

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

// Latency SLI good-signal filter: select the bucket whose `le` label equals
// the SLO threshold. The Microsoft.Monitor/slis 2025-03-01-preview API
// rejects the operator wire values declared in its OpenAPI spec (`==`,
// `<=`, etc.) but accepts the undocumented PascalCase value `EQ`. The
// service also rejects the spec's `values` array property and instead
// requires the undocumented scalar `value`. See ADR-013 and the upstream
// Azure/azure-rest-api-specs issue.
var latencyGoodFilters = [
  {
    dimensionName: 'le'
    operator: 'EQ'
    value: latencyThresholdLe
  }
]

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
    description: 'Request-based Latency SLI for external Azure Functions probe duration. The good signal selects the bucket whose `le` label matches latencyThresholdLe.'
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
            filters: latencyGoodFilters
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
