@description('Deployment location (same region as the VNet and Agent)')
param location string
param tags object
param vaultName string
param vnetId string
param privateEndpointSubnetId string
param workspaceId string
param agentPrincipalId string

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'sre-github-vault'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource endpoint 'Microsoft.Network/privateEndpoints@2025-09-01' = {
  name: 'pe-${vaultName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2026-03-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: zone.id
        }
      }
    ]
  }
  dependsOn: [link]
}

resource signingRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, agentPrincipalId, 'crypto-user')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '12338af0-0e69-4776-bea7-57ae8d297424'
    )
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource audit 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'sre-github-vault-audit'
  scope: vault
  properties: {
    workspaceId: workspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        category: 'AuditEvent'
        enabled: true
      }
    ]
  }
}

output vaultId string = vault.id
output vaultUri string = vault.properties.vaultUri
output vaultNameOut string = vault.name
