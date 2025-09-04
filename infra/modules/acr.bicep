@description('Deployment location')
param location string
@description('Registry name')
param registryName string
@description('Tags object')
param tags object = {}

@description('VNet resource ID used for Private DNS link')
param vnetId string
@description('Private Endpoint subnet resource ID')
param privateEndpointSubnetId string

@description('Principal object IDs to grant AcrPull role (optional)')
param principalObjectIds array = []

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Premium' // Private Endpoint requires Premium SKU
  }
  properties: {
    adminUserEnabled: false
    dataEndpointEnabled: false
    publicNetworkAccess: 'Enabled'
    networkRuleBypassOptions: 'AzureServices'
    encryption: {
      status: 'disabled'
    }
    policies: {
      quarantinePolicy: { status: 'disabled' }
      trustPolicy: { type: 'Notary', status: 'disabled' }
      retentionPolicy: { days: 7, status: 'disabled' }
    }
  }
}

resource containerRegistryPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.azurecr.io'
  location: 'global'
  tags: tags
}

resource containerRegistryPrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: containerRegistryPrivateDnsZone
  name: 'vnetlink-${registryName}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource containerRegistryPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-${registryName}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    #disable-next-line BCP037
    ipVersionType: 'IPv4'
    privateLinkServiceConnections: [
      {
        name: 'plsc-${registryName}'
        properties: {
          privateLinkServiceId: containerRegistry.id
          groupIds: ['registry']
        }
      }
    ]
  }
}

resource containerRegistryPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: containerRegistryPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'registry-config', properties: { privateDnsZoneId: containerRegistryPrivateDnsZone.id } }
    ]
  }
}

output registryId string = containerRegistry.id
output registryNameOut string = containerRegistry.name
output loginServer string = containerRegistry.properties.loginServer

// Optional AcrPull role assignments for provided principals
resource acrPullRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in principalObjectIds: {
    name: guid(containerRegistry.id, principalId, 'AcrPull')
    scope: containerRegistry
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        '7f951dda-4ed3-4680-a7ca-43fe172d538d'
      )
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
