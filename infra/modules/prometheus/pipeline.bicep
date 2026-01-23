@description('Location of the Azure resources')
param location string

@description('Tags that will be applied to all resources')
param tags object = {}

@description('Resource ID of the Azure Monitor managed Prometheus workspace')
param prometheusWorkspaceId string

@description('Resource ID of the AKS cluster')
param aksId string

@description('Name suffix for resources (e.g., appName-environment)')
param nameSuffix string

var aksClusterName = split(aksId, '/')[8]
var dataCollectionEndpointNameBase = 'dce-prom-${nameSuffix}'
var dataCollectionRuleNameBase = 'dcr-prom-${nameSuffix}'
var dataCollectionEndpointName = substring(
  dataCollectionEndpointNameBase,
  0,
  min(44, length(dataCollectionEndpointNameBase))
)
var dataCollectionRuleName = substring(dataCollectionRuleNameBase, 0, min(64, length(dataCollectionRuleNameBase)))
var dataCollectionRuleAssociationName = 'dcra-prom-${nameSuffix}'

resource dataCollectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: dataCollectionEndpointName
  location: location
  tags: tags
  kind: 'Linux'
  properties: {}
}

resource dataCollectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: dataCollectionRuleName
  location: location
  tags: tags
  kind: 'Linux'
  properties: {
    dataCollectionEndpointId: dataCollectionEndpoint.id
    dataFlows: [
      {
        destinations: ['MonitoringAccount1']
        streams: ['Microsoft-PrometheusMetrics']
      }
    ]
    dataSources: {
      prometheusForwarder: [
        {
          name: 'PrometheusDataSource'
          streams: ['Microsoft-PrometheusMetrics']
          labelIncludeFilter: {}
        }
      ]
    }
    description: 'DCR for Azure Monitor Metrics Profile (Managed Prometheus)'
    destinations: {
      monitoringAccounts: [
        {
          accountResourceId: prometheusWorkspaceId
          name: 'MonitoringAccount1'
        }
      ]
    }
  }
}

// Use extension resource with explicit scope to avoid analyzer warnings
@description('Existing AKS cluster for extension resource scope')
#disable-next-line BCP081
resource existingAksCluster 'Microsoft.ContainerService/managedClusters@2025-06-02-preview' existing = {
  name: aksClusterName
}

resource dataCollectionRuleAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2024-03-11' = {
  name: dataCollectionRuleAssociationName
  scope: existingAksCluster
  properties: {
    description: 'Association of data collection rule. Deleting this association will break the data collection for this AKS Cluster.'
    dataCollectionRuleId: dataCollectionRule.id
  }
}

output dataCollectionRuleId string = dataCollectionRule.id
output dataCollectionEndpointId string = dataCollectionEndpoint.id
