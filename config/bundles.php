<?php

/**
 * Symfony bundle registration - tells Symfony which bundles (plugins) to load.
 *
 * Each bundle adds functionality to the application:
 *
 *   FrameworkBundle - Core Symfony framework (routing, DI container, HTTP handling, etc.)
 *   TwigBundle     - Twig template engine for rendering HTML views
 *   StrandsBundle  - Registers StrandsClient services from config/packages/strands.yaml
 *                    (this lives inside strands-php-client, not a separate package)
 *   MercureBundle  - Mercure real-time messaging support (Server-Sent Events hub)
 *
 * The ['all' => true] means the bundle is enabled in ALL environments (dev, prod, test).
 * You could also use ['dev' => true] to only load a bundle in development.
 */

return [
    Symfony\Bundle\FrameworkBundle\FrameworkBundle::class => ['all' => true],
    Symfony\Bundle\TwigBundle\TwigBundle::class => ['all' => true],
    StrandsPhpClient\Integration\Symfony\StrandsBundle::class => ['all' => true],
    Symfony\Bundle\MercureBundle\MercureBundle::class => ['all' => true],
];
