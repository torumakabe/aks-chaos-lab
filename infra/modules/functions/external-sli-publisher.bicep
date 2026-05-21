@description('Deployment location')
param location string

@description('Tags applied to all resources')
param tags object = {}

@description('Workload/application name')
param appName string

@description('Environment name')
param environment string

@description('Unique resource token from the base deployment')
param resourceToken string

@description('VNet resource ID used for private DNS links')
param vnetId string

@description('Delegated subnet resource ID for Flex Consumption VNet integration')
param functionSubnetId string

@description('Private Endpoint subnet resource ID for publisher storage')
param privateEndpointSubnetId string

@description('Application Insights connection string used by the Function App runtime')
param applicationInsightsConnectionString string

@description('Managed Prometheus remote-write URL')
param prometheusRemoteWriteUrl string

@description('URL probed by the external SLI publisher')
param probeUrl string

@description('Probe name used in Prometheus labels')
param probeName string

@description('Probe timeout in seconds')
@minValue(2)
param probeTimeoutSeconds int = 10

@description('Publisher aggregation window size in seconds')
@minValue(60)
param publisherWindowSeconds int = 300

@description('Maximum probe duration counted as good latency')
@minValue(1)
param latencyThresholdMs int = 1000

@description('Maximum closed windows published by one timer invocation')
@minValue(1)
param maxCatchupWindows int = 12

@description('Maximum Flex Consumption scale-out instance count for the publisher')
@minValue(40)
@maxValue(1000)
param maximumInstanceCount int = 40

@description('Flex Consumption instance memory in MB')
@allowed([
  2048
  4096
])
param instanceMemoryMB int = 2048

@description('Timer schedule for the external SLI publisher')
param publisherCronSchedule string = '0 */5 * * * *'

@description('Earliest window start that the publisher may emit. Prevents first deployment from backfilling pre-test windows as bad.')
param signalNotBeforeUtc string = utcNow()

@description('Principal ID allowed to upload the deployment package with azd. Leave empty to skip the deployment principal assignment.')
param deploymentPrincipalId string = ''

var normalizedStorageNameBase = toLower(replace('st${appName}${environment}${resourceToken}', '-', ''))
var storageAccountName = take('${normalizedStorageNameBase}000', 24)
var appServicePlanNameBase = 'aspflex-${appName}-external-sli-${environment}'
var appServicePlanName = substring(appServicePlanNameBase, 0, min(40, length(appServicePlanNameBase)))
var functionAppNameBase = 'func-${appName}-external-sli-${environment}-${resourceToken}'
var functionAppName = substring(functionAppNameBase, 0, min(60, length(functionAppNameBase)))
var stateContainerName = 'external-sli-state'
var deploymentContainerName = 'external-sli-package'
var stateBlobName = 'publisher-state.json'
var stateBlobUrl = 'https://${storageAccount.name}.blob.${az.environment().suffixes.storage}/${stateContainerName}/${stateBlobName}'
var deploymentStorageUrl = '${storageAccount.properties.primaryEndpoints.blob}${deploymentContainerName}'
var storageBlobPrivateDnsZoneName = 'privatelink.blob.${az.environment().suffixes.storage}'
var storageQueuePrivateDnsZoneName = 'privatelink.queue.${az.environment().suffixes.storage}'
var storageTablePrivateDnsZoneName = 'privatelink.table.${az.environment().suffixes.storage}'
var storageBlobDataOwnerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
)
var storageQueueDataContributorRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
)
var storageTableDataContributorRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
)

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
  }
}

resource storageBlobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: storageBlobPrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource storageBlobPrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageBlobPrivateDnsZone
  name: 'vnetlink-blob-${storageAccount.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource storageBlobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2025-05-01' = {
  name: 'pe-blob-${storageAccount.name}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    #disable-next-line BCP037
    ipVersionType: 'IPv4'
    privateLinkServiceConnections: [
      {
        name: 'plsc-blob-${storageAccount.name}'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource storageBlobPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = {
  parent: storageBlobPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob-config', properties: { privateDnsZoneId: storageBlobPrivateDnsZone.id } }
    ]
  }
  dependsOn: [
    storageBlobPrivateDnsZoneLink
  ]
}

resource storageQueuePrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: storageQueuePrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource storageQueuePrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageQueuePrivateDnsZone
  name: 'vnetlink-queue-${storageAccount.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource storageQueuePrivateEndpoint 'Microsoft.Network/privateEndpoints@2025-05-01' = {
  name: 'pe-queue-${storageAccount.name}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    #disable-next-line BCP037
    ipVersionType: 'IPv4'
    privateLinkServiceConnections: [
      {
        name: 'plsc-queue-${storageAccount.name}'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: ['queue']
        }
      }
    ]
  }
}

resource storageQueuePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = {
  parent: storageQueuePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'queue-config', properties: { privateDnsZoneId: storageQueuePrivateDnsZone.id } }
    ]
  }
  dependsOn: [
    storageQueuePrivateDnsZoneLink
  ]
}

resource storageTablePrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: storageTablePrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource storageTablePrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageTablePrivateDnsZone
  name: 'vnetlink-table-${storageAccount.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource storageTablePrivateEndpoint 'Microsoft.Network/privateEndpoints@2025-05-01' = {
  name: 'pe-table-${storageAccount.name}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    #disable-next-line BCP037
    ipVersionType: 'IPv4'
    privateLinkServiceConnections: [
      {
        name: 'plsc-table-${storageAccount.name}'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: ['table']
        }
      }
    ]
  }
}

resource storageTablePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = {
  parent: storageTablePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'table-config', properties: { privateDnsZoneId: storageTablePrivateDnsZone.id } }
    ]
  }
  dependsOn: [
    storageTablePrivateDnsZoneLink
  ]
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  name: 'default'
  parent: storageAccount
}

resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: stateContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: deploymentContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'external-sli-publisher'
  })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    virtualNetworkSubnetId: functionSubnetId
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: deploymentStorageUrl
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.14'
      }
      scaleAndConcurrency: {
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
    }
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: 'https://${storageAccount.name}.blob.${az.environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: 'https://${storageAccount.name}.queue.${az.environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: 'https://${storageAccount.name}.table.${az.environment().suffixes.storage}'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: applicationInsightsConnectionString
        }
        {
          name: 'AZURE_ENV_NAME'
          value: environment
        }
        {
          name: 'EXTERNAL_SLI_CRON_SCHEDULE'
          value: publisherCronSchedule
        }
        {
          name: 'EXTERNAL_SLI_SERVICE_NAME'
          value: 'chaos-app'
        }
        {
          name: 'EXTERNAL_SLI_TELEMETRY_ROLE_NAME'
          value: 'external-sli-publisher'
        }
        {
          name: 'OTEL_SERVICE_NAME'
          value: 'external-sli-publisher'
        }
        {
          name: 'EXTERNAL_SLI_PROBE_URL'
          value: probeUrl
        }
        {
          name: 'EXTERNAL_SLI_PROBE_NAME'
          value: probeName
        }
        {
          name: 'PROMETHEUS_REMOTE_WRITE_URL'
          value: prometheusRemoteWriteUrl
        }
        {
          name: 'EXTERNAL_SLI_STATE_BLOB_URL'
          value: stateBlobUrl
        }
        {
          name: 'EXTERNAL_SLI_WINDOW_SECONDS'
          value: '${publisherWindowSeconds}'
        }
        {
          name: 'EXTERNAL_SLI_PROBE_TIMEOUT_SECONDS'
          value: '${probeTimeoutSeconds}'
        }
        {
          name: 'EXTERNAL_SLI_LATENCY_THRESHOLD_MS'
          value: '${latencyThresholdMs}'
        }
        {
          name: 'EXTERNAL_SLI_MAX_CATCHUP_WINDOWS'
          value: '${maxCatchupWindows}'
        }
        {
          name: 'EXTERNAL_SLI_NOT_BEFORE_UTC'
          value: signalNotBeforeUtc
        }
      ]
    }
  }
  dependsOn: [
    storageBlobPrivateDnsZoneGroup
    storageQueuePrivateDnsZoneGroup
    storageTablePrivateDnsZoneGroup
  ]
}

resource functionStorageBlobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, storageBlobDataOwnerRoleDefinitionId)
  scope: storageAccount
  properties: {
    roleDefinitionId: storageBlobDataOwnerRoleDefinitionId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deploymentPrincipalStorageBlobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deploymentPrincipalId)) {
  name: guid(storageAccount.id, deploymentPrincipalId, storageBlobDataOwnerRoleDefinitionId, 'external-sli-publisher-deploy')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageBlobDataOwnerRoleDefinitionId
    principalId: deploymentPrincipalId
  }
}

resource functionStorageQueueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, storageQueueDataContributorRoleDefinitionId)
  scope: storageAccount
  properties: {
    roleDefinitionId: storageQueueDataContributorRoleDefinitionId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionStorageTableDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, storageTableDataContributorRoleDefinitionId)
  scope: storageAccount
  properties: {
    roleDefinitionId: storageTableDataContributorRoleDefinitionId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output stateBlobUrl string = stateBlobUrl
output publisherPrincipalId string = functionApp.identity.principalId
