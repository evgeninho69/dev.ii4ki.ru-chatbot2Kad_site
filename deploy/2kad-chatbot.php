<?php
/**
 * Plugin Name: 2KAD Chatbot Widget
 * Description: Виртуальный ассистент сайта 2kad.ru — FastAPI-бот на бэкенде.
 * Version: 1.0.0
 * Author: 2KAD
 * MU Plugin: yes
 *
 * Загружает JS-виджет в footer сайта. Конфиг читает из константы
 * KAD_CHATBOT_API_BASE (по умолчанию — '/api' для прокси через этот же домен).
 */

if (!defined('ABSPATH')) {
    return;
}

add_action('wp_enqueue_scripts', function () {
    $api_base = defined('KAD_CHATBOT_API_BASE') ? KAD_CHATBOT_API_BASE : '/api';
    $widget_url = defined('KAD_CHATBOT_WIDGET_URL')
        ? KAD_CHATBOT_WIDGET_URL
        : $api_base . '/widget/widget.js';

    wp_register_script(
        'kad-chatbot-widget',
        esc_url($widget_url),
        array(),
        '1.0.2',
        true
    );

    wp_add_inline_script(
        'kad-chatbot-widget',
        'window.KAD_CHATBOT_CONFIG = ' . wp_json_encode(array(
            'apiBase' => $api_base,
            'title' => 'Ассистент 2КАД',
            'subtitle' => 'Тверь · кадастр · недвижимость',
            'primaryColor' => '#cc2c2c',
            'placeholder' => 'Спросите про межевание, цены, документы…',
        )) . ';',
        'before'
    );

    wp_enqueue_script('kad-chatbot-widget');
});

/*
 * Если бэкенд на другом домене (не reverse-proxy через 2kad.ru),
 * добавь в wp-config.php:

   define('KAD_CHATBOT_API_BASE', 'https://chatbot.2kad.ru/api');
   define('KAD_CHATBOT_WIDGET_URL', 'https://chatbot.2kad.ru/api/widget/widget.js');

 * Если reverse-proxy /api/* → FastAPI backend, тогда всё работает
 * с дефолтами '/api' и '/api/widget/widget.js'.
 */