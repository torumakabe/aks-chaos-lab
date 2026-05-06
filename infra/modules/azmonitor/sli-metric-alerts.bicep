@description('Location for Azure Monitor SLI-generated metric alert resources')
param location string = 'eastus'

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

@description('Availability SLI resource ID')
param availabilitySliId string

@description('Availability SLI resource name')
param availabilitySliName string

@description('Latency SLI resource ID')
param latencySliId string

@description('Latency SLI resource name')
param latencySliName string

@description('Severity for Azure Monitor SLI metric alerts')
@minValue(0)
@maxValue(4)
param severity int = 3

@description('Fast burn-rate alert threshold')
@minValue(1)
param fastBurnRateThreshold int = 14

@description('Fast burn-rate alert lookback in hours')
@minValue(1)
param fastBurnRateLookbackHours int = 1

@description('Slow burn-rate alert threshold')
@minValue(1)
param slowBurnRateThreshold int = 6

@description('Slow burn-rate alert lookback in hours')
@minValue(1)
param slowBurnRateLookbackHours int = 6

var sliDefinitions = [
  {
    name: availabilitySliName
    id: availabilitySliId
  }
  {
    name: latencySliName
    id: latencySliId
  }
]

var actions = actionGroupId != ''
  ? [
      {
        actionGroupId: actionGroupId
      }
    ]
  : []

var sliPromQlCriteria = {
  'odata.type': 'Microsoft.Azure.Monitor.PromQLCriteria'
  allOf: [
    {
      criterionType: 'StaticThresholdCriterion'
      name: 'SliAlertCriterion'
      query: 'up'
    }
  ]
  failingPeriods: {
    for: 'PT5M'
  }
}

resource baselineAlerts 'Microsoft.Insights/metricAlerts@2024-03-01-preview' = [for sli in sliDefinitions: {
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
    criteria: any(sliPromQlCriteria)
    actions: actions
    #disable-next-line BCP037
    customProperties: {
      serviceGroupId: serviceGroupId
      sliId: sli.id
    }
    #disable-next-line BCP037
    resolveConfiguration: {
      autoResolved: true
      timeToResolve: 'PT2M'
    }
  }
}]

resource fastBurnRateAlerts 'Microsoft.Insights/metricAlerts@2024-03-01-preview' = [for sli in sliDefinitions: {
  name: 'SLI fast burn rate alert for ${sli.name}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sliIdentityResourceId}': {}
    }
  }
  properties: {
    description: 'SLI fast burn rate alert for ${sli.name}. Burn rate threshold: ${fastBurnRateThreshold} over the last ${fastBurnRateLookbackHours} hour(s).'
    enabled: true
    severity: severity
    scopes: [
      prometheusWorkspaceResourceId
    ]
    evaluationFrequency: 'PT1M'
    targetResourceType: 'microsoft.monitor/accounts'
    criteria: any(sliPromQlCriteria)
    actions: actions
    #disable-next-line BCP037
    customProperties: {
      burnRate: string(fastBurnRateThreshold)
      lookbackHours: string(fastBurnRateLookbackHours)
      serviceGroupId: serviceGroupId
      sliId: sli.id
    }
    #disable-next-line BCP037
    resolveConfiguration: {
      autoResolved: true
      timeToResolve: 'PT2M'
    }
  }
}]

resource slowBurnRateAlerts 'Microsoft.Insights/metricAlerts@2024-03-01-preview' = [for sli in sliDefinitions: {
  name: 'SLI slow burn rate alert for ${sli.name}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sliIdentityResourceId}': {}
    }
  }
  properties: {
    description: 'SLI slow burn rate alert for ${sli.name}. Burn rate threshold: ${slowBurnRateThreshold} over the last ${slowBurnRateLookbackHours} hour(s).'
    enabled: true
    severity: severity
    scopes: [
      prometheusWorkspaceResourceId
    ]
    evaluationFrequency: 'PT1M'
    targetResourceType: 'microsoft.monitor/accounts'
    criteria: any(sliPromQlCriteria)
    actions: actions
    #disable-next-line BCP037
    customProperties: {
      burnRate: string(slowBurnRateThreshold)
      lookbackHours: string(slowBurnRateLookbackHours)
      serviceGroupId: serviceGroupId
      sliId: sli.id
    }
    #disable-next-line BCP037
    resolveConfiguration: {
      autoResolved: true
      timeToResolve: 'PT2M'
    }
  }
}]

output baselineAlertIds array = [for (sli, i) in sliDefinitions: baselineAlerts[i].id]
output fastBurnRateAlertIds array = [for (sli, i) in sliDefinitions: fastBurnRateAlerts[i].id]
output slowBurnRateAlertIds array = [for (sli, i) in sliDefinitions: slowBurnRateAlerts[i].id]
