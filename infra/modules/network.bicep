@description('Deployment location')
param location string
@description('Tags object')
param tags object
@description('Virtual network name')
param vnetName string
@description('VNet address prefix')
param vnetAddressPrefix string
@description('AKS subnet prefix')
param aksSubnetPrefix string
@description('Private Endpoint subnet prefix')
param peSubnetPrefix string
@description('Resource token for unique naming')
param resourceToken string

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    privateEndpointVNetPolicies: 'Disabled'
  }
}

@description('Network Security Group for AKS subnet')
resource aksSubnetNetworkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-07-01' = {
  name: 'nsg-${vnetName}-snet-aks'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'Allow-HTTP-HTTPS'
        properties: {
          priority: 1000
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRanges: [
            '80'
            '443'
          ]
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

@description('AKS subnet')
resource aksSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' = {
  name: 'snet-aks'
  parent: virtualNetwork
  properties: {
    addressPrefix: aksSubnetPrefix
    networkSecurityGroup: {
      id: aksSubnetNetworkSecurityGroup.id
    }
    privateEndpointNetworkPolicies: 'Disabled'
    defaultOutboundAccess: false
  }
}

@description('Private Endpoint subnet')
resource privateEndpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' = {
  name: 'snet-pe'
  parent: virtualNetwork
  properties: {
    addressPrefix: peSubnetPrefix
    privateEndpointNetworkPolicies: 'Disabled'
    defaultOutboundAccess: false
  }
}

// Public IP for Ingress
resource ingressPublicIP 'Microsoft.Network/publicIPAddresses@2023-04-01' = {
  name: 'pip-ingress-${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: 'aks-chaos-lab-${resourceToken}'
    }
  }
}

output vnetId string = virtualNetwork.id
output aksSubnetId string = aksSubnet.id
output peSubnetId string = privateEndpointSubnet.id
output vnetNameOut string = virtualNetwork.name
output publicIPAddress string = ingressPublicIP.properties.ipAddress
output publicIPId string = ingressPublicIP.id
output fqdn string = ingressPublicIP.properties.dnsSettings.fqdn
output publicIPName string = ingressPublicIP.name
