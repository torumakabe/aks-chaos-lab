@description('Deployment location')
param location string
@description('Tags object')
param tags object
@description('Azure Managed Redis name')
param redisName string
@description('VNet id')
param vnetId string
@description('Private Endpoint subnet id')
param privateEndpointSubnetId string

@description('Principal objectId to grant Redis DB access (optional)')
param principalObjectId string = ''

resource redisEnterprise 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: redisName
  location: location
  tags: tags
  sku: { name: 'Balanced_B0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    minimumTlsVersion: '1.2'
    highAvailability: 'Enabled'
    publicNetworkAccess: 'Disabled'
  }
}

resource redisEnterpriseDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  name: 'default'
  parent: redisEnterprise
  properties: {
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'OSSCluster'
    evictionPolicy: 'NoEviction'
    persistence: { aofEnabled: false, rdbEnabled: false }
    accessKeysAuthentication: 'Disabled'
    deferUpgrade: 'NotDeferred'
  }
}

resource redisPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.redis.azure.net'
  location: 'global'
  tags: tags
}

resource redisPrivateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: redisPrivateDnsZone
  name: 'vnetlink-${redisName}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource redisPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-${redisName}'
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointSubnetId }
    #disable-next-line BCP037
    ipVersionType: 'IPv4'
    privateLinkServiceConnections: [
      {
        name: 'plsc-${redisName}'
        properties: {
          privateLinkServiceId: redisEnterprise.id
          groupIds: ['redisEnterprise']
        }
      }
    ]
  }
}

resource redisPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: redisPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'redis-config', properties: { privateDnsZoneId: redisPrivateDnsZone.id } }
    ]
  }
}

output redisId string = redisEnterprise.id
output redisHost string = redisEnterprise.properties.hostName
output redisPort int = 10000

resource redisAccessPolicyAssignment 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-04-01' = if (!empty(principalObjectId)) {
  name: 'app'
  parent: redisEnterpriseDatabase
  properties: {
    accessPolicyName: 'default'
    user: {
      objectId: principalObjectId
    }
  }
}
