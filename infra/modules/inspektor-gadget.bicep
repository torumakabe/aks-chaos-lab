@description('AKS cluster name')
param aksClusterName string

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2026-03-02-preview' existing = {
  name: aksClusterName
}

resource inspektorGadgetExtension 'Microsoft.KubernetesConfiguration/extensions@2025-03-01' = {
  name: 'inspektor-gadget'
  scope: aksCluster
  properties: {
    extensionType: 'microsoft.inspektorgadget'
    releaseTrain: 'preview'
    autoUpgradeMinorVersion: true
  }
}

output extensionName string = inspektorGadgetExtension.name
