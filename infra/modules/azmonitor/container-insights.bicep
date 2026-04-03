@description('Location of the Azure resources')
param location string

@description('Tags that will be applied to all resources')
param tags object = {}

@description('Resource ID of the AKS cluster')
param aksClusterResourceId string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Name suffix for resources (e.g., appName-environment)')
param nameSuffix string

@description('Data collection preset configuration')
@allowed([
  'All'
  'LogsAndEvents'
  'Custom'
])
param dataCollectionPreset string = 'All'

@description('Data collection interval (valid values: 1m-30m in 1m intervals)')
param dataCollectionInterval string = '1m'

@description('Enable container log version 2')
param enableContainerLogV2 bool = true

@allowed([
  'Off'
  'Include'
  'Exclude'
])
@description('Mode for namespace filtering in data collection')
param namespaceFilteringMode string = 'Off'

@description('Namespaces to include/exclude in data collection')
param namespacesForDataCollection array = []

@description('Enable container network logs collection (ACNS + Cilium required)')
param enableContainerNetworkLogs bool = false

@description('Data streams to collect (used when dataCollectionPreset is Custom)')
param streams array

// Extract cluster name from resource ID for naming
var aksClusterName = split(aksClusterResourceId, '/')[8]
var dataCollectionRuleNameBase = 'dcr-ci-${nameSuffix}'
var dataCollectionRuleName = substring(dataCollectionRuleNameBase, 0, min(64, length(dataCollectionRuleNameBase)))
var dataCollectionRuleAssociationName = 'ContainerInsightsExtension'

// Data Collection Endpoint for high-log-scale-mode (required for container network logs)
var dataCollectionEndpointNameBase = 'dce-ci-${nameSuffix}'
var dataCollectionEndpointName = substring(dataCollectionEndpointNameBase, 0, min(44, length(dataCollectionEndpointNameBase)))

// Extract workspace location from workspace resource ID for DCE placement
var workspaceLocation = location

// Define stream collections based on preset
var logsAndEventsStreams = [
  'Microsoft-ContainerLog'
  'Microsoft-ContainerLogV2'
  'Microsoft-KubeEvents'
  'Microsoft-KubePodInventory'
]

var allStreams = [
  'Microsoft-ContainerLog'
  'Microsoft-ContainerLogV2'
  'Microsoft-KubeEvents'
  'Microsoft-KubePodInventory'
  'Microsoft-ContainerInventory'
  'Microsoft-ContainerNodeInventory'
  'Microsoft-KubeNodeInventory'
  'Microsoft-InsightsMetrics'
  'Microsoft-Perf'
]

var selectedStreams = dataCollectionPreset == 'LogsAndEvents'
  ? logsAndEventsStreams
  : dataCollectionPreset == 'All' ? allStreams : streams

// Add container network logs stream when enabled
// Stream "Microsoft-ContainerNetworkLogs" maps to the ContainerNetworkLogs LA table.
var streamsWithNetworkLogs = enableContainerNetworkLogs
  ? union(selectedStreams, ['Microsoft-ContainerNetworkLogs'])
  : selectedStreams

// Combine streams with KubeMonAgentEvents
var streamWithKubeMonAgentEvents = union(streamsWithNetworkLogs, [
  'Microsoft-KubeMonAgentEvents'
])

// Data Collection Endpoint for high-log-scale-mode ingestion
// Required when container network logs are enabled due to high log volume
resource dataCollectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = if (enableContainerNetworkLogs) {
  name: dataCollectionEndpointName
  location: workspaceLocation
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
    dataCollectionEndpointId: enableContainerNetworkLogs ? dataCollectionEndpoint.id : null
    dataSources: {
      extensions: [
        {
          name: 'ContainerInsightsExtension'
          streams: streamsWithNetworkLogs
          extensionSettings: {
            dataCollectionSettings: {
              interval: dataCollectionInterval
              namespaceFilteringMode: namespaceFilteringMode
              namespacesForDataCollection: namespacesForDataCollection
              enableContainerLogV2: enableContainerLogV2
            }
          }
          extensionName: 'ContainerInsights'
        }
      ]
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalyticsWorkspaceId
          name: 'ciworkspace'
        }
      ]
    }
    dataFlows: [
      {
        streams: streamWithKubeMonAgentEvents
        destinations: [
          'ciworkspace'
        ]
      }
    ]
  }
}

#disable-next-line BCP081
resource existingAksCluster 'Microsoft.ContainerService/managedClusters@2025-08-02-preview' existing = {
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
output dataCollectionRuleName string = dataCollectionRule.name
output dataCollectionRuleAssociationName string = dataCollectionRuleAssociation.name
