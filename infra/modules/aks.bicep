@description('Deployment location')
param location string
@description('AKS name')
param aksName string
@description('Tags object')
param tags object = {}
@description('Kubernetes version (x.y or x.y.z)')
param kubernetesVersion string = '1.33'
@description('Node resource group name for AKS managed resources')
param nodeResourceGroupName string
@description('Node VM size')
param nodeVmSize string
@description('AKS subnet id')
param aksSubnetId string
@description('Log Analytics workspace resource ID for Container Insights')
param logAnalyticsWorkspaceId string

var resourceGroupSuffix = uniqueString(resourceGroup().id)

// User Assigned Managed Identity for AKS cluster
resource aksIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${aksName}'
  location: location
  tags: tags
}

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2025-06-02-preview' = {
  name: aksName
  location: location
  tags: tags
  kind: 'Base'
  // Enable Uptime SLA by switching AKS SKU tier
  sku: {
    name: 'Base'
    tier: 'Standard'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${aksIdentity.id}': {}
    }
  }
  properties: {
    nodeResourceGroup: nodeResourceGroupName
    enableRBAC: true
    supportPlan: 'KubernetesOfficial'
    // Auto-upgrade settings
    autoUpgradeProfile: {
      upgradeChannel: 'patch'
      nodeOSUpgradeChannel: 'NodeImage'
    }
    autoScalerProfile: {
      'balance-similar-node-groups': 'false'
      'daemonset-eviction-for-empty-nodes': false
      'daemonset-eviction-for-occupied-nodes': true
      'ignore-daemonsets-utilization': false
      'max-node-provision-time': '15m'
      'scale-down-delay-after-add': '10m'
      'skip-nodes-with-local-storage': 'false'
    }
    kubernetesVersion: kubernetesVersion
    dnsPrefix: 'dns${substring(resourceGroupSuffix, 0, 8)}'
    oidcIssuerProfile: { enabled: true }
    securityProfile: { workloadIdentity: { enabled: true } }
    networkProfile: {
      // Azure CNI overlay with Cilium dataplane
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      networkPolicy: 'cilium'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
      advancedNetworking: {
        enabled: true
        observability: {
          enabled: true
        }
        security: {
          enabled: true
          advancedNetworkPolicies: 'FQDN'
        }
      }
    }
    ingressProfile: {
      webAppRouting: {
        enabled: true
      }
    }
    metricsProfile: {
      costAnalysis: { enabled: true }
    }
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId
          enableContainerLogV2: 'true'
        }
      }
    }
    // Enable Azure Monitor managed Prometheus (metrics) and app monitoring pipeline
    azureMonitorProfile: {
      containerInsights: {
        enabled: true
        logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceId
      }
      metrics: {
        enabled: true
        kubeStateMetrics: {
          metricAnnotationsAllowList: ''
          metricLabelsAllowlist: ''
        }
      }
      appMonitoring: {
        // Disabled by default to avoid double-instrumentation.
        // Note: The chaos-app in this repository uses manual instrumentation (OpenTelemetry SDK).
        // Flip to true only if you want AKS-managed auto-instrumentation instead.
        autoInstrumentation: {
          enabled: false
        }
        openTelemetryLogs: {
          enabled: true
        }
        openTelemetryMetrics: {
          enabled: true
        }
      }
    }
    agentPoolProfiles: [
      {
        name: 'default'
        vmSize: nodeVmSize
        osType: 'Linux'
        type: 'VirtualMachineScaleSets'
        mode: 'System'
        // Distribute nodes across availability zones (1/2/3 if region supports)
        availabilityZones: [
          '1'
          '2'
          '3'
        ]
        vnetSubnetID: aksSubnetId
        orchestratorVersion: kubernetesVersion
        enableAutoScaling: true
        minCount: 1
        maxCount: 3
      }
    ]
  }
  dependsOn: [
    aksSubnetNetworkContributorRole
  ]
}

// Assign Network Contributor on the AKS subnet to the cluster's managed identity
// Parse subnet and vnet names from the provided subnet resource ID
var subnetIdParts = split(aksSubnetId, '/')
var subnetName = last(subnetIdParts)
var virtualNetworkName = subnetIdParts[8]

@description('Existing VNet (parent of the AKS subnet)')
resource existingVirtualNetwork 'Microsoft.Network/virtualNetworks@2024-07-01' existing = {
  name: virtualNetworkName
}

@description('Existing AKS subnet (role assignment scope)')
resource existingAksSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' existing = {
  name: subnetName
  parent: existingVirtualNetwork
}

// Network Contributor role definition ID
var networkContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4d97b98b-1d4f-4787-a291-c67834d212e7'
)

resource aksSubnetNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingAksSubnet.id, 'NetworkContributor', aksIdentity.name)
  scope: existingAksSubnet
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Assign Network Contributor on the resource group for managing public IPs
resource aksResourceGroupNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, 'NetworkContributor', aksIdentity.name)
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Output AKS cluster identity principal ID for additional role assignments
output aksIdentityPrincipalId string = aksIdentity.properties.principalId

// AKS Managed Auto-Upgrade Schedule (monthly on first Wednesday)
#disable-next-line BCP081
resource aksMaintenanceConf 'Microsoft.ContainerService/managedClusters/maintenanceConfigurations@2025-06-02-preview' = {
  parent: aksCluster
  name: 'aksManagedAutoUpgradeSchedule'
  properties: {
    maintenanceWindow: {
      durationHours: 4
      schedule: {
        relativeMonthly: {
          dayOfWeek: 'Wednesday'
          intervalMonths: 1
          weekIndex: 'First'
        }
      }
      startDate: '2025-04-01'
      startTime: '00:00'
      utcOffset: '+09:00'
    }
  }
}

// AKS Managed Node OS Upgrade Schedule (weekly on Wednesday)
#disable-next-line BCP081
resource aksMaintenanceNodeConf 'Microsoft.ContainerService/managedClusters/maintenanceConfigurations@2025-06-02-preview' = {
  parent: aksCluster
  name: 'aksManagedNodeOSUpgradeSchedule'
  properties: {
    maintenanceWindow: {
      durationHours: 4
      schedule: {
        weekly: {
          dayOfWeek: 'Wednesday'
          intervalWeeks: 1
        }
      }
      startDate: '2025-04-01'
      startTime: '00:00'
      utcOffset: '+09:00'
    }
  }
}

output aksId string = aksCluster.id
output aksNameOut string = aksCluster.name
output kubeletObjectId string = aksCluster.properties.identityProfile.kubeletidentity.objectId
output oidcIssuerUrl string = aksCluster.properties.oidcIssuerProfile.issuerURL
